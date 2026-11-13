# Структура проекта AI Seller Core

ТЗ backend — источник требований: [`BACKEND_MVP_TZ.md`](BACKEND_MVP_TZ.md).

Ниже приведена **целевая структура модульного монолита MVP-1**. Метки показывают текущий статус:

- `[есть]` — файл или модуль уже создан в репозитории;
- `[частично]` — есть начальная основа/контракт, но нет полного поведения;
- `[план]` — целевой модуль, который ещё предстоит реализовать.

Дерево описывает файлы приложения и документации, а не физически созданные сейчас файлы. Новые модули добавляются по мере реализации требований ТЗ.

## Корень репозитория

```text
ai_manager/
├── .env.example                         [есть] Пример локальной конфигурации без секретов
├── .gitignore                           [есть] Исключения для окружения, секретов и кешей
├── alembic.ini                          [есть] Конфигурация миграций PostgreSQL
├── pyproject.toml                       [есть] Проект, зависимости, настройки тестов и Ruff
├── uv.lock                              [есть] Зафиксированные версии зависимостей
├── README.md                            [есть] Запуск и краткий статус разработки
├── docs/                                Документация и архитектурные решения
├── migrations/                          Миграции схемы PostgreSQL
├── src/ai_seller_core/                  Исходный код backend
└── tests/                               Unit, integration, contract и E2E тесты
```

## Исходный код backend

```text
src/ai_seller_core/
├── __init__.py                          [есть]
├── main.py                              [есть] Создание FastAPI-приложения
│
├── api/                                 HTTP API и webhook ingress
│   ├── __init__.py                      [есть]
│   ├── health.py                        [есть] Health endpoint
│   ├── deps.py                          [план] DI: DB session, auth, tenant context
│   └── v1/                              [план] Версионированный REST API
│       ├── accounts.py                  [план] Аккаунты, проекты, роли
│       ├── agents.py                    [план] Черновики, публикация и релизы агента
│       ├── channels.py                  [план] Подключение и состояние каналов
│       ├── catalog.py                   [план] Импорт и поиск каталога
│       ├── knowledge.py                 [план] Документы и база знаний
│       ├── dialogs.py                   [план] История диалогов и операторские действия
│       ├── leads.py                     [план] Внутренние заявки
│       ├── webhooks.py                  [план] Webhook ingress MAX
│       └── router.py                    [план] Сборка API router
│
├── core/                                Общие настройки и инфраструктурные контракты
│   ├── __init__.py                      [есть]
│   ├── config.py                        [есть] Настройки приложения из окружения
│   ├── security.py                      [план] JWT/scopes, шифрование секретов
│   ├── logging.py                       [план] Структурные логи и маскирование
│   ├── errors.py                        [план] Общий формат ошибок API и домена
│   └── telemetry.py                     [план] Метрики, trace и correlation ID
│
├── domain/                              Доменные модели, события и правила
│   ├── accounts.py                      [план] Account, Project, memberships
│   ├── agents.py                        [план] Agent, draft, immutable release
│   ├── channels.py                      [план] Channel, capabilities, health
│   ├── events.py                        [план] InboundEvent и доменные события
│   ├── dialogs.py                       [план] Session, message, escalation
│   ├── catalog.py                       [план] Product, source, import
│   ├── customers.py                     [план] Customer, identity, согласие
│   └── leads.py                         [план] Lead и lead items
│
├── persistence/                         PostgreSQL и репозитории
│   ├── __init__.py                      [есть]
│   ├── base.py                           [есть] Async SQLAlchemy engine/session
│   ├── models.py                         [частично] Inbox, jobs, Outbox, session
│   ├── session_lease.py                  [частично] Lease и fencing token сессии
│   ├── repositories/                     [план] Репозитории по доменным границам
│   │   ├── inbox.py                     [план]
│   │   ├── jobs.py                      [план]
│   │   ├── outbox.py                    [план]
│   │   ├── dialogs.py                   [план]
│   │   └── catalog.py                   [план]
│   └── transactions.py                  [план] Границы бизнес-транзакций
│
├── jobs/                                Durable jobs, Inbox и Outbox delivery
│   ├── __init__.py                      [есть]
│   ├── inbox.py                          [частично] Дедупликация и постановка job
│   ├── inbox_handler.py                  [частично] Обработчик сессии с injected runtime/resolvers
│   ├── outbox.py                         [частично] Атомарная запись сообщения и job
│   ├── delivery.py                       [частично] Контур доставки; policy implementation required
│   ├── repository.py                     [частично] Enqueue и атомарный claim job
│   ├── retry.py                          [есть] Exponential backoff с jitter
│   ├── worker.py                         [частично] Polling, lease, heartbeat, retry
│   ├── registry.py                       [план] Регистрация job handlers
│   ├── runner.py                         [план] Запуск worker-профилей
│   └── scheduler.py                      [план] Повторы, SLA, health и lease recovery
│
├── dialog/                              Диалоговый runtime
│   ├── __init__.py                      [есть]
│   ├── runtime.py                        [частично] LLM/tool loop и sanitizer contract
│   ├── orchestration.py                  [план] Полный сценарий обработки сообщения
│   ├── session_service.py                [частично] Lease, lifecycle и fenced commit turn
│   ├── context_builder.py                [план] Очищенный ограниченный контекст модели
│   ├── response.py                       [план] CanonicalResponse и проверка capabilities
│   ├── policy.py                         [план] Лимиты, pause, статус агента и SLA
│   ├── escalation.py                     [план] Передача оператору и operator pause
│   └── tools/                            Серверные инструменты модели
│       ├── registry.py                   [план] Реестр доступных функций
│       ├── executor.py                   [план] Проверка и выполнение tool calls
│       ├── search_products.py             [план]
│       ├── get_product.py                 [план]
│       ├── create_lead.py                 [план]
│       └── escalate_to_operator.py        [план]
│
├── llm/                                 Один выбранный provider в MVP-1
│   ├── __init__.py                      [план]
│   ├── contracts.py                      [план] Provider protocol и общие DTO
│   ├── router.py                         [план] Лимиты, подготовка и вызов модели
│   ├── adapter.py                        [план] Преобразование форматов API
│   ├── providers/
│   │   ├── base.py                       [план] Контракт адаптера
│   │   └── openai_compatible.py          [план] Адаптер выбранного совместимого API
│   ├── privacy.py                        [план] Очистка данных до LLM-запроса
│   ├── budgets.py                        [план] Резервирование лимитов и учёт расходов
│   └── usage.py                          [план] Токены, длительность и ошибки вызова
│
├── channels/                            Каналы и F31 adapter framework
│   ├── __init__.py                      [план]
│   ├── contracts.py                      [план] ChannelAdapter, event и response
│   ├── registry.py                       [план] Реестр адаптеров
│   ├── event_router.py                   [план] Маршрутизация типизированных событий
│   └── max/
│       ├── client.py                     [план] MAX Bot API client
│       ├── models.py                     [план] DTO MAX API и webhook
│       ├── webhook.py                    [план] Проверка и нормализация webhook
│       ├── adapter.py                    [план] ChannelAdapter для MAX
│       └── converter.py                  [план] Канонические сообщения ↔ MAX
│
├── catalog/                             Каталог товаров
│   ├── __init__.py                      [план]
│   ├── service.py                        [план] Авторитетное чтение товаров
│   ├── search.py                         [план] Фильтры + семантический retrieval
│   ├── importer.py                       [план] CSV/XLSX import pipeline
│   ├── mapping.py                        [план] Mapping полей источника
│   └── providers.py                      [план] ProductDataProvider contract/impl
│
├── knowledge/                           База знаний и RAG
│   ├── __init__.py                      [план]
│   ├── documents.py                      [план] Загрузка и жизненный цикл документов
│   ├── extraction.py                     [план] Извлечение поддерживаемых форматов
│   ├── chunking.py                       [план] Разбиение и версионирование фрагментов
│   ├── embeddings.py                     [план] Embedding provider contract
│   ├── retrieval.py                      [план] Поиск релевантных фрагментов
│   └── vector_store.py                   [план] Интерфейс векторного хранилища
│
├── customers/                           Покупатели, ПДн и заявки
│   ├── __init__.py                      [план]
│   ├── service.py                        [план] Профиль и identity покупателя
│   ├── consent.py                        [план] Согласия с версией документа
│   ├── privacy.py                        [план] Удаление и очистка данных
│   └── leads.py                          [план] Внутренние заявки MVP-1
│
├── workers/                             Composition root фоновых процессов
│   ├── __init__.py                      [план]
│   ├── dialog.py                         [план] Очередь inbound_dialog
│   ├── delivery.py                      [план] Очередь outbound_delivery
│   ├── catalog.py                       [план] Очередь catalog_import
│   ├── indexing.py                      [план] Очередь indexing
│   └── scheduler.py                     [план] Плановые задачи
│
└── observability/                       [план] Сервисная диагностика и аудит
    ├── audit.py                          [план]
    ├── metrics.py                        [план]
    └── health.py                         [план] Readiness внешних зависимостей
```

## Миграции и тесты

```text
migrations/
├── env.py                                [есть] Alembic async environment
├── script.py.mako                        [есть] Шаблон миграций
└── versions/
    ├── 0001_initial_durable_processing.py [есть] Базовые таблицы jobs/Inbox/Outbox/session
    ├── 7612d753d48f_add_session_lifecycle_fields_and_chat_.py [есть] Поля lifecycle и история сообщений
    └── 0002_billing_schema.py [план] Тарифы, модели LLM, подписки, Dancoin, инвойсы, платежи, usage, agent_slots

tests/
├── test_health.py                        [есть] Health endpoint
├── test_retry.py                         [есть] Retry delay
├── test_dialog_runtime.py                [есть] Sanitization и tool round-trip
├── unit/                                 [план] Доменные правила и компоненты
├── integration/                          [план] PostgreSQL, Inbox/Outbox, workers
├── contracts/                            [план] MAX, LLM и HTTP API contracts
└── e2e/                                  [план] Сквозные сценарии MVP из ТЗ
```

## Документы

```text
docs/
├── README.md                             Этот индекс и целевая структура проекта
├── BACKEND_MVP_TZ.md                     Источник истины по backend MVP
├── core-architecture.md                  Архитектурные решения
├── EXTERNAL_INTEGRATIONS.md              Спецификация внешних интеграций
└── SERVICE_BLOCK_DIAGRAM.md              Блок-схемы сервиса и потоков
```

## Границы запуска

Это **модульный монолит**, а не набор микросервисов. Один код приложения запускается разными процессами:

| Профиль | Очереди/назначение |
|---|---|
| `api` | REST API и быстрый durable webhook ingress |
| `worker-dialog` | `inbound_dialog`, Dialog Runtime, tools и постановка ответа в Outbox |
| `worker-delivery` | `outbound_delivery`, повторяемая отправка MAX |
| `worker-catalog` | Импорт и проверка полноты каталога |
| `worker-indexing` | Embeddings, индексация и удаление векторов |
| `scheduler` | SLA, повторные задачи, восстановление lease, health checks, **billing jobs (гранты, списания, инвойсы, grace period)** |

PostgreSQL является источником истины для бизнес-данных, Inbox, Outbox и фоновых задач. Redis в MVP-1 не обязателен и может использоваться только как ускоритель пробуждения/координации; потеря Redis не должна терять задания. Qdrant содержит восстанавливаемые поисковые индексы, а не авторитетные цены и остатки.

## Что не входит в целевой MVP-1

CRM/ERP и маркетплейс-коннекторы, мультипровайдерная маршрутизация LLM, визуальный редактор сценариев, биллинг и фронтенд, автоматическое межканальное объединение покупателей, а также другие точки роста, отложенные в [`BACKEND_MVP_TZ.md`](BACKEND_MVP_TZ.md).
