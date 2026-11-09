# Блок-схема сервиса «ИИ-продавец»

## 1. Общая схема сервиса

Сервис реализуется как модульный backend: API и разные профили worker запускаются отдельными процессами одного приложения. В MVP-1 подключается только MAX; CRM и другие каналы остаются за границами первой версии. PostgreSQL хранит исходные бизнес-данные и состояния обработки, Qdrant — производные векторные индексы.

```mermaid
flowchart TB
    subgraph External["Внешние системы и пользователи"]
        Buyer["Покупатель"]
        Front["Фронт / клиентский интерфейс"]
        MAX["MAX"]
        LLMProvider["Один выбранный LLM provider / gateway"]
        EmbedProvider["Embedding provider"]
    end

    subgraph Entry["Вход и доставка"]
        Edge["Edge proxy / TLS"]
        API["Core API<br/>API управления / webhook MAX"]
        Adapter["F31 Channel Adapter Registry<br/>проверка источника • нормализация • capabilities"]
        Realtime["SSE / WebSocket / polling"]
    end

    subgraph Core["Backend — модульный монолит"]
        Access["Auth • scopes • tenant context<br/>accounts / projects / entitlements"]
        Inbox["Inbox + idempotency<br/>дедупликация внешних событий"]
        EventRouter["Event Router<br/>тип события → handler"]
        ChannelEvents["Channel lifecycle / status handlers"]
        Runtime["Dialog Runtime<br/>состояние сессии • история • правила"]
        Tools["Tool Registry / Executor<br/>проверка схемы и прав функций"]
        Policy["Policy / Rules Engine<br/>лимиты • расписание • pause • escalation"]
        Privacy["Form gate + PII sanitizer<br/>ПДн не передаются в LLM"]
        Composer["Response Composer<br/>текст • карточки • кнопки"]
        Customer["Customer Service<br/>форма • профиль • согласие"]
        Lead["Lead Service<br/>внутренняя заявка"]
        Outbox["Outbox Dispatcher<br/>доставка • повторы • dead letter"]
        Import["Catalog Importer<br/>CSV / XLSX"]
        Catalog["Catalog Service<br/>поиск • проверка коммерческих данных"]
        KB["Knowledge Service / Indexer"]
        Indexer["Indexing worker<br/>embeddings • Qdrant"]
        LLMRouter["LLM Router"]
        Scheduler["Scheduler<br/>SLA • retries • lease recovery • health MAX"]
        Telemetry["Audit • logs • metrics"]
        Health["Channel Health Monitor<br/>healthy • degraded • failed • requires_setup • disabled"]
    end

    subgraph Storage["Хранение и фоновые процессы"]
        PG[("PostgreSQL<br/>business data • inbox • jobs • outbox")]
        Jobs["Durable background_jobs<br/>lease • retries • priority"]
        Qdrant[("Qdrant<br/>каталог • база знаний")]
        FileStore[("Закрытое файловое хранилище<br/>на сервере в MVP-1")]
        Redis[("Redis — необязательно<br/>worker wake-up")]
        Workers["Worker pools<br/>dialog • delivery • catalog • indexing"]
    end

    Buyer --> MAX
    Front --> Edge
    MAX --> Edge
    Edge --> API
    API --> Access
    API --> Adapter
    Adapter --> Inbox
    Inbox --> PG
    Inbox --> Jobs
    API --> PG
    API --> Realtime
    Realtime --> Front

    Jobs --> Workers
    PG <--> Workers
    Redis -. "ускорение, не источник истины" .-> Workers
    Workers --> EventRouter
    EventRouter -->|message / callback| Privacy
    EventRouter -->|channel lifecycle / status| ChannelEvents
    ChannelEvents --> PG
    ChannelEvents --> Health
    Health --> PG
    Health --> Telemetry

    Runtime --> Policy
    Runtime <--> PG
    Privacy -->|"ПДн формы"| Customer
    Privacy -->|"только очищенный текст"| Runtime
    Runtime --> Tools
    Tools --> Catalog
    Tools --> KB
    Tools --> Lead
    Runtime <--> LLMRouter
    Import --> PG
    Import --> FileStore
    Import --> Jobs
    Import --> Indexer
    Catalog --> PG
    Catalog --> Qdrant
    KB --> PG
    KB --> FileStore
    KB --> Indexer
    Indexer --> EmbedProvider
    Indexer --> Qdrant
    LLMRouter <--> LLMProvider
    LLMRouter --> PG
    Runtime --> Composer
    Composer --> PG
    Composer --> Outbox
    Lead --> PG
    Outbox --> Jobs
    Workers --> Outbox
    Outbox --> Adapter
    Adapter --> MAX
    Scheduler --> Jobs
    Scheduler --> PG

    Access --> Telemetry
    Runtime --> Telemetry
    Workers --> Telemetry
    Outbox --> Telemetry
```

### Обозначение узлов

- **Core API** — предоставляет API фронту и принимает webhook MAX. Не ждёт завершения генерации ответа внутри webhook-запроса.
- **F31 Adapter Registry** — переводит протокол конкретного мессенджера в канонический формат ядра и обратно. Не выбирает товары и не формирует коммерческую логику.
- **Inbox** — сохраняет принятое событие и предотвращает повторную обработку одного события поставщика.
- **Event Router** — направляет message/callback события MAX в диалоговый runtime, а события жизненного цикла канала и статуса — в специализированные обработчики. Немесседжные события не превращаются в сообщения с пустым текстом; неизвестные события сохраняются как unsupported/ignored с диагностикой.
- **Channel Health Monitor** — хранит состояния `healthy`, `degraded`, `failed`, `requires_setup`, `disabled` и подтверждённую причину сбоя (`failure_domain`); временный outage не требует от клиента менять корректные credentials.
- **Durable `background_jobs`** — задания для worker. PostgreSQL хранит их надёжно; Redis, если подключён, используется только как вспомогательный транспорт/сигнал.
- **Dialog Runtime** — управляет жизненным циклом сообщения: состояние сессии, история, правила, вызов функций и получение результата.
- **Tool Executor** — исполняет только разрешённые функции с серверной валидацией аргументов. Модель не обращается к БД и интеграциям напрямую.
- **Catalog / Knowledge** — каталог хранит подтверждённые характеристики, цену и наличие; база знаний отвечает на консультационные вопросы.
- **Response Composer** — формирует канонический ответ и проверяет, что коммерческие значения взяты из доверенного источника.
- **Outbox** — фиксирует исходящие ответы MAX для асинхронной доставки. CRM-доставка не входит в MVP-1.
- **Worker pools** — отдельные процессы одного backend-приложения. Диалоговые задачи отделены от импорта и индексации, чтобы тяжёлая обработка каталога не задерживала покупателя.

## 2. Обработка входящего события

```mermaid
flowchart TD
    Buyer["Покупатель отправляет событие в MAX"] --> MAX["MAX Bot API"]
    MAX --> API["Core API и MAX adapter"]
    API --> Verify["Проверить webhook, источник и формат"]
    Verify --> StoreInbound["PostgreSQL: атомарно сохранить Inbox и durable job"]
    StoreInbound --> Ack["Подтвердить webhook после сохранения"]
    StoreInbound --> Worker["Worker забирает job с lease"]
    Worker --> Route{"Event Router по типу события"}

    Route -->|"Событие формы"| FormData["Сохранить данные формы в профиле покупателя"]
    FormData --> PGForm[("PostgreSQL")]
    Route -->|"Message или callback"| Sanitizer["Проверить заполнение формы;<br/>очистить входящий текст от ПДн"]

    Sanitizer --> FormCheck{"Форма заполнена?"}
    FormCheck -->|"Нет"| WaitForm["Не запускать Dialog Runtime и LLM;<br/>ожидать заполнения формы"]
    WaitForm --> FormReply["Попросить заполнить обязательную форму"]
    FormReply --> Outbox
    FormCheck -->|"Да"| SanitizeCheck{"Текст очищен?"}
    SanitizeCheck -->|"Нет"| NoLLM["Не передавать исходный текст в LLM;<br/>попросить переформулировать или эскалировать"]
    NoLLM --> Outbox
    SanitizeCheck -->|"Да"| Runtime["Dialog Runtime загружает историю,<br/>состояние и закреплённый release"]

    Runtime --> Policy{"Агент активен;<br/>лимиты позволяют ответ;<br/>нет эскалации или operator pause?"}
    Policy -->|"Нет"| Stop["Сохранить состояние / эскалацию;<br/>LLM не вызывать"]
    Stop --> PersistStop["PostgreSQL: зафиксировать событие"]
    Policy -->|"Да"| Context["Сформировать очищенный контекст<br/>и передать MAX capabilities"]
    Context --> LLM["Один LLM provider через внутренний интерфейс"]
    LLM --> ModelResult{"Ответ или tool call?"}
    ModelResult -->|"Ответ"| Compose["Response Composer"]
    ModelResult -->|"search_products"| ProductTool["Tool Executor: search_products"]
    ModelResult -->|"search_knowledge"| KBTool["Tool Executor: search_knowledge"]
    ProductTool --> Catalog["Семантический поиск Qdrant;<br/>точные поля и цена из PostgreSQL"]
    Catalog --> ToolResult["Вернуть проверенные данные модели"]
    KBTool --> Knowledge["Найти фрагменты базы знаний<br/>с фильтром project_id"]
    Knowledge --> KBResult["Вернуть релевантные фрагменты модели"]
    ToolResult --> LLM
    KBResult --> LLM
    LLM --> Compose

    Compose --> Validate["Проверить коммерческие данные,<br/>формат MAX и актуальность ответа"]
    Validate --> PersistResponse["Короткая транзакция: история, состояние,<br/>событие и Outbox с проверкой fencing token"]
    PersistResponse --> Outbox["worker-delivery забирает Outbox job"]
    Outbox --> Preflight["Непосредственно перед отправкой проверить:<br/>актуальность диалога, эскалацию,<br/>operator pause и deadline"]
    Preflight --> CanSend{"Отправка разрешена?"}
    CanSend -->|"Нет"| Hold["Удержать, отменить или пометить expired;<br/>сохранить причину"]
    Hold --> PGDelivery[("PostgreSQL: статус Outbox")]
    CanSend -->|"Да"| Send["Отправить ответ через MAX adapter"]
    Send --> SendResult{"Исход отправки"}
    SendResult -->|"Успех"| Delivered["Сохранить delivered и remote message ID"]
    Delivered --> MAX
    SendResult -->|"Временная ошибка"| Retry["Ограниченный retry с backoff,<br/>если повтор безопасен"]
    Retry --> PGDelivery
    SendResult -->|"Неизвестен"| Unknown["delivery_unknown;<br/>не повторять вслепую"]
    Unknown --> PGDelivery
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
9. MVP-1 включает только канал MAX. Ozon, Avito, CRM и Wildberries не участвуют в этом потоке; новые подключения добавляются отдельными этапами через адаптеры.
10. Payload Inbox и технические данные webhook не являются обычной историей диалога: ПДн в них минимизируются, шифруются или удаляются по установленной политике хранения.

## 3. Поток загрузки каталога

```mermaid
flowchart LR
    Operator["Оператор / кабинет"] --> API["Core API: создать импорт"]
    API --> Storage["Закрытый каталог файлов на сервере"]
    API --> PG[("PostgreSQL: catalog_import + background_job")]
    PG --> Worker["worker-catalog"]
    Worker --> Parse["CSV / XLSX parser"]
    Parse --> Mapping["Mapping полей + нормализация"]
    Mapping --> Validation{"Валидация строки"}
    Validation -->|"валидна"| Upsert["Upsert товара<br/>project_id + source_id + external_id"]
    Validation -->|"ошибка"| Error["catalog_import_errors"]
    Upsert --> PG
    Upsert --> Changed{"Изменился поисковый текст?"}
    Changed -->|"да"| IndexJob["Создать indexing job"]
    Changed -->|"нет, только цена/остаток"| Done["Индекс не пересчитывать"]
    IndexJob --> NextRow{"Есть ещё строки?"}
    Done --> NextRow
    Error --> NextRow
    NextRow -->|"да"| Parse
    NextRow -->|"нет"| Complete{"Полный проход и критерии полноты<br/>успешно выполнены?"}
    Complete -->|"нет / ошибки влияют на полноту"| Keep["Не деактивировать отсутствующие товары"]
    Complete -->|"да"| Deactivate["Деактивировать отсутствующие товары"]
    IndexJob --> Indexer["worker-indexing"]
    Indexer --> Embed["Embedding provider"]
    Embed --> Qdrant[("Qdrant: производный индекс")]
    Error --> Report["Статус и отчёт импорта"]
    Done --> Report
    Keep --> Report
    Deactivate --> Report
    Qdrant --> Report
    Report --> API
    API --> Operator
```

PostgreSQL хранит текущие товарные данные; Qdrant используется только для поиска/ранжирования и восстанавливается из первичных данных. Коммерческие поля для ответа покупателю не берутся из векторного индекса.

## 4. Поток создания внутренней заявки (MVP-1)

```mermaid
sequenceDiagram
    participant Buyer as Покупатель
    participant MAX as MAX
    participant Runtime as Dialog Runtime
    participant Tool as create_lead tool
    participant PG as PostgreSQL
    participant Customer as Customer Service

    Buyer->>MAX: Явно подтвердить создание заявки
    MAX->>Runtime: Доставить подтверждённое действие в диалог
    Runtime->>Tool: Передать поля потребности и выбранные товары
    Tool->>Tool: Проверить подтверждение, согласие,<br/>сессию, проект и fencing token
    Tool->>Customer: Получить только необходимые данные профиля
    Customer->>PG: Прочитать данные формы
    Tool->>PG: Одна транзакция: lead + lead_items + event<br/>ПДн остаются внутри сервиса
    PG-->>Tool: Заявка зафиксирована локально
    Tool-->>Runtime: Успешный результат create_lead
    Runtime-->>Buyer: Подтвердить регистрацию заявки
    Note over PG: CRM Outbox, Bitrix24 sink и CRM IDs<br/>не входят в MVP-1
```

Заявка хранится внутри сервиса и доступна через API проекта. Её создание требует явного подтверждения покупателя; вызов модели сам по себе не считается подтверждением. CRM-доставка относится к MVP-2.

## 5. Поток настройки интеграции и конфигурации

```mermaid
flowchart LR
    Front["Фронт (технология не ограничена)"] -->|API управления| API["Core API"]
    API --> Auth["Auth • scopes • tenant context"]
    Auth --> Config["Account & Project Management"]
    Config --> Validate["Проверить настройки и entitlements"]
    Validate -->|валидно| Secrets["Зашифровать credentials"]
    Secrets --> PG[("PostgreSQL")]
    Config --> Audit["Audit log"]
    Config --> Choice{"Тип подключения"}
    Choice -->|MAX| MaxCheck["Проверить MAX credentials<br/>и webhook configuration"]
    MaxCheck --> MAX["MAX Bot API"]
    MAX -->|результат проверки| Config
    Choice -->|LLM| LLMCheck["Проверить один выбранный provider/model<br/>и доступные capabilities"]
    LLMCheck --> LLM["LLM provider / gateway"]
    LLM -->|результат проверки| Config
    Config --> Status["Состояние MAX channel:<br/>healthy / degraded / failed / requires_setup / disabled<br/>+ failure_domain"]
    Config --> Model["Конфигурация единственной модели MVP-1"]
    Status --> API
    Model --> API
    API --> Front
```

Секреты доступны только серверному адаптеру при вызове API. Они не возвращаются кабинету повторно в открытом виде, не передаются в LLM и не включаются в логи.

## 6. Обработка ошибок и повторы

```mermaid
flowchart TD
    Start["Worker атомарно забрал job с lease"] --> Execute["Вызвать handler вне долгой DB-транзакции"]
    Execute --> Result{"Результат"}
    Result -->|"Успех"| Fence{"Lease/fencing token<br/>ещё актуален?"}
    Fence -->|"да"| Commit["В одной транзакции зафиксировать<br/>состояние и завершить job"]
    Fence -->|"нет"| Reject["Отклонить устаревший результат worker"]
    Result -->|"Ошибка"| Classify{"Ошибка временная<br/>и запрос безопасно повторить?"}
    Classify -->|"нет"| Dead["dead_letter"]
    Classify -->|"да"| RetryCheck{"Попытки ещё доступны?"}
    RetryCheck -->|"да"| Schedule["Рассчитать backoff + jitter;<br/>записать available_at"]
    Schedule --> Pending["Статус retrying; задача остаётся в PostgreSQL"]
    RetryCheck -->|"нет"| Dead
    Result -->|"Неизвестен исход отправки"| Unknown["Outbox: delivery_unknown<br/>без слепого повтора"]
    Dead --> Alert["Сохранить код/причину и метрики"]
    Pending --> WorkerLoop["Worker заберёт позже"]
    Unknown --> Alert
```

Внешние HTTP-вызовы выполняются с таймаутами и ограничением конкурентности. Смена владельца session lease определяется выдачей нового fencing token. Истёкшее время lease само по себе token не меняет. Неопределённый исход отправки MAX фиксируется как `delivery_unknown`; автоматический повтор допустим только при подтверждённой идемпотентности либо после сверки статуса.

## 7. Что запускается при разработке и в production

| Профиль запуска | Основная работа |
|---|---|
| `api` | API фронта, webhook ingress MAX, управление и чтение истории |
| `worker-dialog` | `inbound_dialog`: входящие MAX events, сессии, Dialog Runtime, LLM и tools |
| `worker-delivery` | `outbound_delivery`: ответы MAX через Outbox |
| `worker-catalog` | `catalog_import`: импорт CSV/XLSX и отчёты |
| `worker-indexing` | `indexing`: embeddings и индексация/удаление Qdrant vectors |
| `scheduler` | Повторы, SLA, lease recovery и health MAX |
| PostgreSQL | Бизнес-данные, история, Inbox, durable jobs, Outbox и аудит |
| Qdrant | Векторные индексы; источник данных — PostgreSQL/документы |
| Закрытый каталог файлов на сервере | Исходные файлы импорта и документы в MVP-1 |
| Redis (необязательно) | Только сигнал для пробуждения worker; при недоступности jobs продолжают обрабатываться polling из PostgreSQL |

Все профили — процессы одного модульного монолита и запускаются на MVP-1 напрямую на Linux-сервере под `systemd`; контейнеризация не используется. Профили могут быть сведены в меньшее количество процессов при разработке, но webhook/API и долгие фоновые задачи остаются разделены логически. CRM delivery, Ozon, Avito, виджет и другие каналы не входят в MVP-1.
