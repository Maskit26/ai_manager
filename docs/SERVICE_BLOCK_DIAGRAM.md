# Блок-схема сервиса «ИИ-продавец»

## 1. Общая схема сервиса

Сервис реализуется как модульный backend: API и разные профили worker запускаются отдельными процессами, но используют общие доменные модули. Внешние мессенджеры и CRM подключаются адаптерами. PostgreSQL хранит исходные бизнес-данные и состояния обработки, Qdrant — производные векторные индексы.

```mermaid
flowchart TB
    subgraph External["Внешние системы и пользователи"]
        Buyer["Покупатель"]
        Admin["Кабинет / Laravel"]
        MAX["MAX"]
        Ozon["Ozon Chat"]
        Avito["Avito Messenger"]
        B24["Bitrix24 CRM"]
        WB["Wildberries<br/>точка роста"]
        LLMProvider["Провайдер LLM"]
        EmbedProvider["Провайдер embeddings"]
    end

    subgraph Entry["Вход и доставка"]
        Edge["Edge proxy / TLS"]
        API["Core API<br/>REST / Widget / Webhook"]
        Adapter["F31 Channel Adapter Registry<br/>проверка источника • нормализация • capabilities"]
        Realtime["SSE / WebSocket / polling"]
    end

    subgraph Core["Backend — модульный монолит"]
        Access["Auth • scopes • tenant context<br/>accounts / projects / entitlements"]
        Inbox["Inbox + idempotency<br/>дедупликация внешних событий"]
        EventRouter["Event Router<br/>тип события → handler"]
        IntegrationEvents["Integration Event Handlers<br/>CRM • lifecycle • status"]
        Runtime["Dialog Runtime<br/>состояние сессии • история • правила"]
        Tools["Tool Registry / Executor<br/>проверка схемы и прав функций"]
        Policy["Policy / Rules Engine<br/>лимиты • расписание • pause • escalation"]
        Composer["Response Composer<br/>текст • карточки • кнопки"]
        Lead["Customer / Lead Service<br/>клиент • согласие • заявка"]
        CRMAdapter["LeadSink: Bitrix24"]
        Outbox["Outbox Dispatcher<br/>доставка • повторы • dead letter"]
        Import["Catalog Importer<br/>CSV / XLSX / YML"]
        KB["Knowledge Service / Indexer"]
        LLMRouter["LLM Router"]
        Scheduler["Scheduler<br/>плановые импорты • SLA • retries"]
        Telemetry["Audit • logs • metrics"]
        Health["Channel Health Monitor<br/>healthy • degraded • failed • requires_setup"]
    end

    subgraph Storage["Хранение и фоновые процессы"]
        PG[("PostgreSQL<br/>business data • inbox • jobs • outbox")]
        Jobs["Durable background_jobs<br/>lease • retries • priority"]
        Qdrant[("Qdrant<br/>каталог • база знаний")]
        ObjectStore[("Object storage<br/>исходные файлы и документы")]
        Redis[("Redis — опционально<br/>wake-up • rate limit • realtime pub/sub")]
        Workers["Worker pools<br/>dialog • delivery • catalog • indexing"]
    end

    Buyer --> MAX
    Buyer --> Ozon
    Buyer --> Avito
    MAX --> Edge
    Ozon --> Edge
    Avito --> Edge
    Admin --> Edge
    Edge --> API
    API --> Access
    API --> Adapter
    Adapter --> Inbox
    Inbox --> PG
    Inbox --> Jobs
    API --> PG
    API --> Realtime
    Realtime --> Admin

    Jobs --> Workers
    PG <--> Workers
    Redis -. "ускорение, не источник истины" .-> Workers
    Workers --> EventRouter
    EventRouter -->|message / callback| Runtime
    EventRouter -->|CRM / lifecycle / status| IntegrationEvents
    IntegrationEvents --> PG
    IntegrationEvents --> Health
    Health --> PG
    Health --> Telemetry

    Runtime --> Policy
    Runtime <--> PG
    Runtime --> Tools
    Tools --> Import
    Tools --> KB
    Tools --> Lead
    Tools --> LLMRouter
    Import --> PG
    Import --> ObjectStore
    Import --> Jobs
    Import --> Qdrant
    KB --> PG
    KB --> ObjectStore
    KB --> Qdrant
    KB --> EmbedProvider
    LLMRouter <--> LLMProvider
    LLMRouter --> PG
    Runtime --> Composer
    Composer --> PG
    Composer --> Outbox
    Lead --> PG
    Lead --> Outbox
    Outbox --> Jobs
    Workers --> Outbox
    Outbox --> Adapter
    Outbox --> CRMAdapter
    Adapter --> MAX
    Adapter --> Ozon
    Adapter --> Avito
    CRMAdapter --> B24
    Scheduler --> Jobs
    Scheduler --> PG

    Access --> Telemetry
    Runtime --> Telemetry
    Workers --> Telemetry
    Outbox --> Telemetry
```

### Обозначение узлов

- **Core API** — принимает запросы кабинета и виджета, а также webhook внешних платформ. Не ждёт завершения генерации ответа внутри webhook-запроса.
- **F31 Adapter Registry** — переводит протокол конкретного мессенджера в канонический формат ядра и обратно. Не выбирает товары и не формирует коммерческую логику.
- **Inbox** — сохраняет принятое событие и предотвращает повторную обработку одного события поставщика.
- **Event Router** — направляет сообщения и callbacks в диалоговый runtime, а CRM-, lifecycle-, delivery-status и системные события — в соответствующие обработчики. Немесседжные события не превращаются в сообщения с пустым текстом.
- **Channel Health Monitor** — различает временную деградацию внешнего провайдера и исправимые настройки подключения; показывает причину, если она подтверждена.
- **Durable `background_jobs`** — задания для worker. PostgreSQL хранит их надёжно; Redis, если подключён, используется только как вспомогательный транспорт/сигнал.
- **Dialog Runtime** — управляет жизненным циклом сообщения: состояние сессии, история, правила, вызов функций и получение результата.
- **Tool Executor** — исполняет только разрешённые функции с серверной валидацией аргументов. Модель не обращается к БД и интеграциям напрямую.
- **Catalog / Knowledge** — каталог хранит подтверждённые характеристики, цену и наличие; база знаний отвечает на консультационные вопросы.
- **Response Composer** — формирует канонический ответ и проверяет, что коммерческие значения взяты из доверенного источника.
- **Outbox** — фиксирует сообщения и события, которые необходимо доставить во внешнюю систему, и обеспечивает безопасные повторы.
- **Worker pools** — отдельные процессы одного backend-приложения. Диалоговые задачи отделены от импорта и индексации, чтобы тяжёлая обработка каталога не задерживала покупателя.

## 2. Обработка входящего события

```mermaid
flowchart TD
    Buyer["Покупатель пишет вопрос"] --> Channel["MAX / Ozon / Avito"]
    Channel -->|Webhook / update| API["Core API"]
    API --> Adapter["F31 Adapter<br/>Проверка источника по правилам канала и формата"]
    Adapter --> Normalize["Нормализация<br/>UnifiedEvent"]
    Normalize --> Persist["PostgreSQL: сохранить Inbox<br/>и background_job в одной транзакции"]
    Persist --> Ack["Подтвердить приём каналу"]
    Ack --> Worker["worker<br/>забрать задачу с lease"]
    Worker --> Router{"Event Router<br/>event_type"}
    Router -->|message / callback| Resolve["Разрешить customer/session<br/>и порядок обработки"]
    Router -->|CRM / lifecycle / status / notice| DomainHandler["Специализированный handler<br/>обновить состояние / аудит"]
    DomainHandler --> PersistState["PostgreSQL: записать результат"]
    Resolve --> WorkerDialog["worker-dialog"]
    WorkerDialog --> Runtime["Dialog Runtime<br/>загрузить сессию, историю, конфигурацию"]
    Runtime --> Policy{"Policy / Rules<br/>Разрешено отвечать?"}

    Policy -->|Нет: пауза / stop / оператор| Escalate["Сохранить состояние<br/>и создать эскалацию при необходимости"]
    Escalate --> PersistState["PostgreSQL: записать событие"]

    Policy -->|Да| Constraints["Channel Capabilities<br/>формат • длина • SLA • доступные функции"]
    Constraints --> LLM["LLM Router<br/>получает ограничения до генерации"]
    LLM --> Decision{"Нужны данные?"}
    Decision -->|Поиск товара| ToolProduct["Tool Executor<br/>search_products"]
    ToolProduct --> Catalog["Catalog Service<br/>поиск по характеристикам"]
    Catalog --> ProductData["PostgreSQL<br/>актуальные карточки, цена, наличие"]
    ProductData --> LLM

    Decision -->|Консультация| ToolKB["Tool Executor<br/>search_knowledge"]
    ToolKB --> KB["Knowledge Service<br/>поиск с фильтром project_id"]
    KB --> KBData["Фрагменты и источники"]
    KBData --> LLM

    Decision -->|Данных не требуется| Compose["Response Composer"]
    LLM -->|Ответ сформирован| Compose
    ProductData -->|Подтверждённые данные| Compose
    KBData -->|Контекст документов| Compose
    Compose --> Verify["Повторно проверить цену и наличие<br/>по авторитетному источнику"]
    Verify --> Validate["Проверить ответ по channel constraints<br/>без молчаливого обрезания"]
    Validate --> Save["PostgreSQL: сохранить историю,<br/>состояние и Outbox"]
    Save --> Delivery["worker-delivery<br/>Outbox Dispatcher"]
    Delivery --> SLA{"Срок ответа канала<br/>ещё не истёк?"}
    SLA -->|Нет| Expired["Outbox: expired<br/>эскалация / фиксация причины"]
    SLA -->|Да| Adapter
    Adapter --> Send["API отправки в канал"]
    Send --> Channel
    Channel --> Buyer
    Delivery --> Status["Записать статус доставки<br/>и внешний message ID"]
    Status --> Realtime["Опубликовать обновление истории<br/>для polling / SSE / WebSocket"]
```

### Важные свойства потока

1. Webhook подтверждается только после сохранения типизированного события и durable background job в PostgreSQL.
2. Повторный webhook не создаёт второе сообщение и второй запуск обработки.
3. Долгий вызов LLM не удерживает соединение с платформой мессенджера.
4. Диалог обрабатывается последовательно в рамках одной сессии; независимые сессии работают параллельно.
5. Модель может предложить вызвать tool, но сервер проверяет схему, права и контекст перед исполнением.
6. Цена, валюта, наличие и характеристики при отправке ответа повторно читаются из авторитетного каталога или provider. Если значение неизвестно или устарело, оно не выдаётся как факт.
7. Отправка ответа выполняется через Outbox после фиксации истории. Ошибка канала обновляет статус доставки и запускает ограниченную retry policy.
8. Состояние `degraded` обозначает временный сбой провайдера и не требует от клиента менять credentials. `requires_setup` используется для подтверждённой проблемы настройки, прав или подписки.
9. Wildberries остаётся точкой роста; транспорт и набор поддерживаемых операций определяются только отдельной спецификацией после проверки официального API.

## 3. Поток загрузки каталога

```mermaid
flowchart LR
    Operator["Оператор / кабинет"] --> API["Core API: создать импорт"]
    API --> Storage["Object storage: сохранить исходный файл"]
    API --> PG[("PostgreSQL: catalog_import + background_job")]
    PG --> Worker["worker-catalog"]
    Worker --> Parse["CSV / XLSX / YML parser"]
    Parse --> Mapping["Mapping полей + нормализация"]
    Mapping --> Validation{"Валидация строки"}
    Validation -->|"валидна"| Upsert["Upsert товара<br/>project_id + source_id + external_id"]
    Validation -->|"ошибка"| Error["catalog_import_errors"]
    Upsert --> PG
    Upsert --> Changed{"Изменился поисковый текст?"}
    Changed -->|"да"| IndexJob["Создать indexing job"]
    Changed -->|"нет, только цена/остаток"| Done["Индекс не пересчитывать"]
    IndexJob --> Indexer["worker-indexing"]
    Indexer --> Embed["Embedding provider"]
    Embed --> Qdrant[("Qdrant: производный индекс")]
    Error --> Report["Статус и отчёт импорта"]
    Done --> Report
    Qdrant --> Report
    Report --> API
    API --> Operator
```

PostgreSQL хранит текущие товарные данные; Qdrant используется только для поиска/ранжирования и восстанавливается из первичных данных. Коммерческие поля для ответа покупателю не берутся из векторного индекса.

## 4. Поток создания заявки и Bitrix24

```mermaid
sequenceDiagram
    participant Runtime as Dialog Runtime
    participant Tool as create_lead tool
    participant PG as PostgreSQL
    participant Worker as worker-delivery
    participant Sink as Bitrix24 LeadSink
    participant B24 as Bitrix24 REST API

    Runtime->>Tool: Передать собранные поля заявки
    Tool->>Tool: Проверить поля, согласие, сессию и проект
    Tool->>PG: Одна транзакция: сохранить lead + outbox
    PG-->>Tool: Заявка сохранена локально
    Tool-->>Runtime: Подтверждение регистрации заявки
    Worker->>PG: Забрать CRM outbox item
    Worker->>Sink: Передать нормализованный lead
    Sink->>B24: Создать/обновить контакт и лид/сделку
    B24-->>Sink: Результат и CRM ID
    Sink->>PG: Сохранить CRM ID и статус доставки
    alt Временная ошибка CRM
        Sink->>PG: Запланировать повтор с backoff
    else Постоянная ошибка CRM
        Sink->>PG: Пометить failed/dead_letter и записать причину
    end
```

Покупателю можно подтвердить создание заявки после успешного сохранения во внутренней БД. Доступность Bitrix24 не должна приводить к потере заявки или блокировать текущий ответ.

## 5. Поток настройки интеграции и конфигурации

```mermaid
flowchart LR
    Admin["Laravel / кабинет"] -->|API управления| API["Core API"]
    API --> Auth["Auth • scopes • tenant context"]
    Auth --> Config["Account & Project Management"]
    Config --> Validate["Проверить настройки и entitlements"]
    Validate -->|валидно| Secrets["Зашифровать credentials"]
    Secrets --> PG[("PostgreSQL")]
    Config --> Audit["Audit log"]
    Config --> Test["Проверить подключение / зарегистрировать webhook"]
    Test --> Provider["MAX / Ozon / Avito / Bitrix24"]
    Provider -->|результат проверки| Config
    Config --> Status["Статус подключения:<br/>active / error / requires_setup"]
    Status --> API
    API --> Admin
```

Секреты доступны только серверному адаптеру при вызове API. Они не возвращаются кабинету повторно в открытом виде, не передаются в LLM и не включаются в логи.

## 6. Обработка ошибок и повторы

```mermaid
flowchart TD
    Start["Worker забрал задачу с lease"] --> Execute["Вызвать внутренний handler или внешний API"]
    Execute --> Result{"Результат"}
    Result -->|"Успех"| Commit["Зафиксировать результат и завершить задачу"]
    Result -->|"Временная ошибка"| RetryCheck{"Попытки ещё доступны?"}
    RetryCheck -->|"да"| Schedule["Рассчитать backoff + jitter;<br/>записать available_at"]
    Schedule --> Pending["Вернуть задачу в pending/retrying"]
    RetryCheck -->|"нет"| Dead["dead_letter / failed"]
    Result -->|"Постоянная ошибка"| Dead
    Dead --> Alert["Статус интеграции / метрика / диагностика"]
    Pending --> WorkerLoop["Worker заберёт позже"]
```

Внешние HTTP-вызовы выполняются с таймаутами и ограничением конкурентности. Повтор допускается только с идемпотентной операцией либо с механизмом сопоставления результата, предотвращающим дубли.

## 7. Что запускается при разработке и в production

| Профиль запуска | Основная работа |
|---|---|
| `api` | Управляющий API, публичный API сообщений, webhook ingress и чтение истории |
| `worker-dialog` | Входящие сообщения, сессии, runtime, LLM и инструменты |
| `worker-delivery` | Исходящие сообщения, CRM, webhook-уведомления и повторы Outbox |
| `worker-catalog` | Импорт и нормализация каталога |
| `worker-indexing` | Индексация/удаление векторов каталога и базы знаний |
| `scheduler` | Плановые задания, SLA и запуск готовых повторов |
| PostgreSQL | Бизнес-данные, история, Inbox, durable jobs, Outbox и аудит |
| Qdrant | Векторные индексы; источник данных — PostgreSQL/документы |
| Object storage | Исходные файлы импорта и документы |
| Redis (опционально) | Ускорение пробуждения worker, rate limiting и real-time pub/sub; не источник истины |

Профили могут быть сведены в меньшее количество процессов на раннем этапе разработки, но API webhook и долгие фоновые задачи должны оставаться разделены логически. Перед production диалоговую обработку следует изолировать от импорта и индексации.
