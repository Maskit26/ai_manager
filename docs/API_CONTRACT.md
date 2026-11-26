# API Contract — draft

**Status:** черновик для согласования  
**Версия документа:** 0.1  
**Основа:** Часть 4 `BACKEND_MVP_TZ.md`, API billing из Части 2.5, текущие auth, persistence и dialog services.

Этот документ фиксирует предлагаемый внешний контракт API. Маршруты и поля, помеченные **TBD**, требуют согласования до реализации соответствующего endpoint. API не должен дублировать бизнес-правила сервисного слоя.

## 1. Принятые решения и границы

### Подтверждено

- До переноса на сервер интеграционные проверки выполняются с fake-провайдерами.
- На сервере рабочей БД будет копия Clover. Тестовые операции не должны очищать или пересоздавать эту БД; миграции и тестовые данные выполняются контролируемо.
- Для первого серверного smoke test подключается реальный MAX; если подключение невозможно — Telegram. Входящие сообщения должны пройти до обработки worker-ом.
- На первом серверном smoke test LLM и Qdrant допускаются fake; фактическая отправка ответа в мессенджер не является критерием прохождения.
- API для фронта и webhook мессенджера входят в интерфейс диалогов.
- Real-time транспорт для фронта — WebSocket.
- Первичное создание account и owner выполняется через отдельный защищённый server-to-server onboarding API. Его credential не передаётся браузеру.
- Быстрые чтения и короткие операции с БД выполняются непосредственно HTTP API. В фоновые задания выносятся долгие операции, прежде всего обработка входящего сообщения.

### Не входит в первоначальную проверку

- Публичный web-widget API.
- Реальная отправка сообщений из backend в MAX/Telegram.
- Реальные LLM/Qdrant во время локальных проверок до переноса на сервер.
- CRM и другие внешние интеграции.

Реальное подключение MAX, а при необходимости Telegram, является следующим deployment-шагом, а не утверждением, что такие адаптеры уже реализованы.

## 2. Общие правила HTTP API

- Базовый префикс: `/api/v1`.
- Формат запросов и ответов: JSON, UTF-8. Webhook payload сначала проверяется адаптером соответствующего провайдера.
- Идентификаторы backend-ресурсов передаются как UUID. Время — RFC 3339 в UTC.
- `account_id` для обычных tenant-facing запросов берётся из проверенного principal. Клиент не может выбрать или переопределить tenant через body, query или произвольный header.
- Защищённые ответы не содержат credentials, токены провайдеров или секреты каналов. Секреты, если endpoint их принимает, показываются только при создании/обновлении и никогда не возвращаются при чтении.
- Health endpoints могут оставаться вне `/api/v1`.

### Синхронный запрос или фоновая задача

**Синхронно** отвечают запросы, которые быстро завершаются работой с backend DB:

- получение списка проектов, участников, диалогов, сообщений и статусов;
- чтение истории с пагинацией;
- короткое создание или изменение ресурса, если для него не требуется внешняя/длительная операция.

**Асинхронно** выполняются операции, которые могут долго ждать worker, LLM, обработчик файла или внешний provider:

- обработка входящего сообщения;
- длительный импорт/разбор документа;
- операции, выполняемые отдельным background worker.

Для асинхронного запроса API сначала фиксирует данные и durable job в PostgreSQL, затем возвращает `202 Accepted`. Durable job — это сохранённая в БД задача, которую worker может забрать после перезапуска сервиса. HTTP-запрос не ждёт выполнения workflow.

### DB-сессия запроса и сессия диалога — разные понятия

Каждый HTTP handler, которому нужна БД, получает короткоживущую SQLAlchemy `AsyncSession` через request dependency. Она используется для чтения или короткой транзакции и закрывается после HTTP-запроса. Поэтому быстрый `GET /dialogs` также открывает DB-сессию, чтобы прочитать данные.

`DialogSession` — отдельная долговечная сущность, представляющая разговор с клиентом. `GET /dialogs` читает её, но не создаёт и не изменяет. По действующему правилу ТЗ жизненным циклом `DialogSession` владеет только Dialog Worker; API передаёт ему durable job для операций, требующих создания/возобновления сессии. Таким образом, API использует DB-сессии, но не становится вторым владельцем жизненного цикла клиентских диалогов.

### Фабрика DB-сессий и process lifecycle

`AsyncEngine`, `SessionFactory` и `get_session()` определены в общей инфраструктуре `persistence/base.py`. FastAPI-приложение и CLI фонового Worker запускают `database_lifespan()` каждый в своём процессе. Если API и Worker — разные процессы, у каждого свой экземпляр engine и фабрики в памяти; они используют общую реализацию и настройки подключения к PostgreSQL, а не передают друг другу Python-объекты сессий.

FastAPI получает request-scoped `AsyncSession` через `get_session()` и закрывает её в конце запроса. Worker получает отдельную task-scoped `AsyncSession` из своей фабрики на время операции и закрывает её после неё. DB-сессия API никогда не передаётся worker-у, и worker не управляет её lifecycle.

### Формат успешного ответа

- `200 OK` — синхронное чтение или изменение с телом ответа.
- `201 Created` — синхронно созданный ресурс.
- `202 Accepted` — запрос принят и сохранён для фоновой обработки; ответ содержит `operation_id`.
- `204 No Content` — успешная операция без тела ответа.

## 3. Аутентификация и авторизация

### Пользовательский API

Защищённые endpoints принимают:

```http
Authorization: Bearer <access-token>
```

Используется текущая проверка JWT: RS256, `kid`, `iss`, `aud`, `iat`, `nbf`, `exp`, scopes и ограничение TTL. `sub` — внешний immutable ID пользователя Bitrix; `account_id` — canonical backend UUID. `sub` разрешается через identity mapping; затем backend проверяет действующий membership и роль. Неизвестный или неоднозначный `sub` и отсутствие membership отклоняются. Fallback на email/name запрещён.

**Совместимость формата `sub` — TBD:** текущий verifier дополнительно требует, чтобы `sub` был UUID. До согласования контракта нужно подтвердить фактический формат Bitrix ID. Если Bitrix выдаёт не UUID, требование identity mapping должно быть приведено в соответствие с verifier до подключения фронта.

Tenant-facing доступ проверяет одновременно scope, роль, принадлежность ресурса к account и, для диалога, право пользователя на конкретный диалог. Для operator сохраняется проверка назначения на диалог, если она требуется текущей access policy.

### Первичный onboarding

Предлагаемый endpoint:

```http
POST /api/v1/internal/onboarding/accounts
Authorization: Bearer <server-to-server-credential>
Idempotency-Key: <key>
```

Он создаёт account, backend user, Bitrix identity mapping и initial owner membership. Это отдельная server-to-server аутентификация, не пользовательский JWT. Credential хранится только на доверенных backend-серверах.

**TBD:** механизм выпуска/ротации credential, его владельцы и deployment-конфигурация. До согласования onboarding endpoint не должен быть доступен браузерному клиенту.

### Webhooks

Webhook не использует пользовательский JWT. Он проверяет provider-specific подпись/secret и соответствие `channel_id` проекту. Точный алгоритм проверки для MAX/Telegram фиксируется при подключении реального provider.

### Предлагаемые scopes

Scopes проверяются вместе с текущей ролью из backend DB. Названия ниже — предложение для согласования:

| Scope | Доступ |
|---|---|
| `account:read` | Текущий account и его настройки |
| `members:read` | Список участников |
| `members:write` | Добавление, изменение роли и удаление участников |
| `projects:read` | Чтение проектов |
| `projects:write` | Создание и изменение проектов |
| `dialogs:read` | Чтение доступных диалогов и истории |
| `dialogs:write` | Операторские сообщения и заметки |
| `agents:read`, `agents:write` | Чтение агента и изменение draft/release |
| `channels:read`, `channels:write` | Чтение и управление каналами |
| `catalog:read`, `catalog:write` | Чтение и изменение каталога |
| `knowledge:read`, `knowledge:write` | Чтение и управление базой знаний |
| `leads:read`, `leads:write` | Чтение и изменение заявок |
| `analytics:read` | Аналитика |
| `billing:read`, `billing:write` | Billing endpoints |
| `operations:read` | Проверка статуса фоновых операций |

Точный scope каждого endpoint и окончательная role × scope matrix — TBD.

## 4. Ошибки

Предлагаемый единый формат ошибок:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed",
    "request_id": "uuid",
    "details": [
      {
        "field": "name",
        "reason": "required"
      }
    ]
  }
}
```

`details` может отсутствовать. Текст ошибки и детали не должны раскрывать секреты, PII, внутренние SQL-ошибки или наличие чужого tenant-ресурса.

Предлагаемое отображение HTTP-статусов:

| HTTP | Случай |
|---|---|
| `400` | Некорректный формат запроса, который не покрывается schema validation |
| `401` | Нет или невалидна аутентификация |
| `403` | Недостаточно scope/роли |
| `404` | Ресурс не найден или недоступен этому tenant |
| `409` | Конфликт состояния или повтор Idempotency-Key с другим телом |
| `413` | Превышен размер тела/файла |
| `415` | Неподдерживаемый Content-Type |
| `422` | Ошибка валидации DTO |
| `429` | Rate limit |
| `500` | Неожиданная внутренняя ошибка |
| `402` | Нет активной подписки/баланса, если этого требует Entitlements Engine |

Точные доменные коды ошибок и HTTP-маппинг должны быть согласованы с существующими доменными исключениями и Entitlements Engine.

## 5. Идемпотентность и фоновые операции

### Idempotency-Key

Для повторяемых `POST`-операций предлагается заголовок `Idempotency-Key`. Он обязателен для:

- первичного provisioning;
- создания ресурса или заявки;
- создания/запуска диалога;
- приёма frontend-сообщения;
- операторского сообщения/заметки, если запрос может безопасно повториться после timeout.

Для webhook dedupe выполняется по provider event ID и channel/project; повторное событие не создаёт второе входящее сообщение или job. Повтор с тем же ключом и тем же телом возвращает исходный результат; тот же ключ с другим телом — `409`.

Текущая БД ограничивает `idempotency_key` и `provider_event_id` длиной 200 символов. Предлагается не принимать более 200 символов без отдельной миграции; формат ключа и период хранения — TBD. Если официальный provider event ID может быть длиннее, потребуется расширить колонку до подключения этого provider.

### Статус операции

Предлагаемый endpoint:

```http
GET /api/v1/operations/{operation_id}
```

Он синхронно возвращает публичный статус фоновой операции и безопасное для клиента описание ошибки. Внутренние job payload и stack traces не раскрываются.

Предлагаемые публичные статусы: `queued`, `processing`, `succeeded`, `failed`. Их соответствие внутренним статусам `background_jobs` — TBD.

## 6. Диалоги, сообщения и входящие события

### Правило владения сессией

В соответствии с текущим правилом ТЗ API не создаёт и не изменяет `chat_sessions`/`chat_messages` напрямую. API/webhook валидирует и нормализует событие, записывает Inbox и durable job; Dialog Worker создаёт/находит сессию и обрабатывает событие. Историю API читает из БД синхронно.

Это правило необходимо сверить с формулировкой Части 4 «создать/найти сессию». Предложение: создание/возобновление сессии доступно как команда, переданная worker-у, а не как прямое изменение сессии из HTTP handler.

### Webhook провайдера

Предлагаемый маршрут:

```http
POST /api/v1/webhooks/{provider}/{channel_id}
```

`provider` для начального подключения: `max`; при невозможности подключения — `telegram`. Provider-specific payload преобразуется адаптером в нормализованное входящее событие.

Последовательность:

1. Проверить provider signature/secret и принадлежность канала проекту.
2. Проверить обязательные поля и ограничения размера.
3. В одной транзакции зафиксировать Inbox event и durable processing job.
4. Только после commit вернуть `202 Accepted` (или обязательный provider handshake response).
5. Не запускать LLM и не ждать worker внутри HTTP-запроса.

Идемпотентность provider events: уникальность `(project_id, channel_id, provider_event_id)`.

Предлагаемый ответ:

```json
{
  "event_id": "uuid",
  "operation_id": "uuid",
  "message_id": null,
  "dialog_id": null,
  "status": "accepted"
}
```

`message_id`/`dialog_id` могут быть `null`, пока worker не создал сообщение/сессию. После обработки доступен `operation_id` и событие по WebSocket/история диалога.

### Предлагаемые dialog endpoints

| Method | Path | Назначение | Режим |
|---|---|---|---|
| `GET` | `/api/v1/projects/{project_id}/dialogs` | Список доступных диалогов; фильтры по статусу/назначению | Синхронно |
| `GET` | `/api/v1/dialogs/{dialog_id}` | Метаданные доступного диалога | Синхронно |
| `GET` | `/api/v1/dialogs/{dialog_id}/messages` | История сообщений с cursor pagination | Синхронно |
| `POST` | `/api/v1/projects/{project_id}/dialogs` | Запросить создание/поиск диалога через worker; не создаёт сессию в HTTP handler | `202` |
| `POST` | `/api/v1/dialogs/{dialog_id}/messages` | Операторское сообщение; автор берётся из principal, не из body | `202`; заблокировано до миграции role model |
| `POST` | `/api/v1/dialogs/{dialog_id}/notes` | Операторская заметка/системное уведомление | `202`; заблокировано до миграции role model |

Для операторского доступа проверяется account/project/dialog scope и назначение, если оно обязательно по роли. Поле `role` в запросе не принимается от клиента.

**Блокер реализации:** текущий `ChatMessage.role` допускает только `user` и `assistant`; для требований ТЗ о различении сообщений `operator`/`system` потребуется согласовать модель, аудит и миграцию до реализации соответствующих write endpoints.

### Пагинация

Предлагается opaque cursor, стабильный порядок по `(created_at, id)`, параметры `limit` и `cursor`, ответ:

```json
{
  "items": [],
  "next_cursor": null
}
```

Размер страницы по умолчанию и максимум — TBD.

## 7. WebSocket

Предлагаемый endpoint:

```http
GET /api/v1/ws
```

Соединение tenant-scoped. Подписка на dialog IDs проверяется backend-ом; клиент не может подписаться на чужой диалог.

Предлагаемые события:

- `message.created`;
- `message.delivery_updated`;
- `operation.updated`.

Пример server event:

```json
{
  "type": "message.created",
  "event_id": "uuid",
  "occurred_at": "2026-01-01T12:00:00Z",
  "data": {
    "dialog_id": "uuid",
    "message_id": "uuid"
  }
}
```

Для браузерного WebSocket предлагается короткоживущий одноразовый connection ticket, получаемый по обычному JWT через `POST /api/v1/realtime/tickets`. Это позволяет не передавать долгоживущий JWT в query string. Срок жизни, формат подписки, heartbeat, reconnect/cursor semantics и лимит соединений — TBD.

## 8. API управления

Маршруты ниже — целевой inventory API Части 4. Этот документ фиксирует требования и открытые решения независимо от текущего статуса реализации; фактическое состояние реализации ведётся в `DATA_MODEL_DESIGN.md`. DTO конкретных бизнес-ресурсов описываются отдельно на основе их application services и моделей; отсутствие сервиса не должно компенсироваться бизнес-логикой внутри router.

### Account и membership

| Method | Path | Назначение |
|---|---|---|
| `GET` | `/api/v1/account` | Текущий account по principal |
| `GET` | `/api/v1/account/members` | Список участников |
| `POST` | `/api/v1/account/members` | Добавление участника |
| `PATCH` | `/api/v1/account/members/{user_id}` | Изменение роли/статуса |
| `DELETE` | `/api/v1/account/members/{user_id}` | Удаление/отключение membership |

Здесь `member` означает участника команды **аккаунта** (tenant), а не участника клиентского диалога. Это запись `account_memberships`, привязанная к canonical backend user из `users`; внешний Bitrix ID хранится отдельно в `user_identities` и разрешается через identity mapping. Роли: `owner`, `admin`, `operator`; состояние membership: `active` или `disabled`.

Добавление account member не назначает его автоматически на проекты или диалоги. Доступ оператора к диалогам регулируется отдельно назначениями проекта/диалога. Предлагаемое тело `POST /api/v1/account/members` принимает canonical backend `user_id` и требуемую роль; `account_id` берётся из principal, а не из запроса.

Правила ролей реализуются существующими membership services. Владелец и администратор не взаимозаменяемы; запрет на удаление/понижение последнего owner сохраняется.

### Projects

| Method | Path | Назначение |
|---|---|---|
| `GET` | `/api/v1/projects` | Список проектов текущего account |
| `POST` | `/api/v1/projects` | Создание проекта |
| `GET` | `/api/v1/projects/{project_id}` | Получение проекта |
| `PATCH` | `/api/v1/projects/{project_id}` | Обновление проекта |
| `POST` | `/api/v1/projects/{project_id}/archive` | Архивация |
| `DELETE` | `/api/v1/projects/{project_id}` | Запрос удаления/retention flow |
| `POST` | `/api/v1/projects/{project_id}/restore` | Восстановление в установленный период |

Все операции проверяют `project.account_id == principal.account_id`. Удаление и восстановление используют существующую persistence-логику; публичные маршруты требуются backlog item Части 4.

### Agent, channels, catalog, knowledge base, leads, analytics

Первоначальный маршрутный inventory:

| Область | Предлагаемый path prefix |
|---|---|
| Agent draft/release | `/api/v1/projects/{project_id}/agent` |
| Channels | `/api/v1/projects/{project_id}/channels` |
| Catalog | `/api/v1/projects/{project_id}/catalog` |
| Knowledge base | `/api/v1/projects/{project_id}/knowledge` |
| Leads | `/api/v1/projects/{project_id}/leads` |
| Analytics | `/api/v1/projects/{project_id}/analytics` |

Подробные методы и поля payload — TBD после сверки с application services каждого модуля. Для длительного импорта, публикации или обработки документа возвращается `202` и operation ID. Секреты channel credentials никогда не возвращаются в read DTO.

### Billing

Эти endpoints входят в контракт Части 4 по API из Части 2.5:

| Method | Path |
|---|---|
| `GET` | `/api/v1/billing/plans` |
| `GET` | `/api/v1/billing/subscription` |
| `GET` | `/api/v1/billing/dancoin` |
| `POST` | `/api/v1/billing/subscription/change` |
| `POST` | `/api/v1/billing/checkout` |
| `POST` | `/api/v1/billing/dancoin/purchase` |
| `POST` | `/api/v1/billing/refund-requests` |
| `GET` | `/api/v1/billing/invoices` |
| `GET` | `/api/v1/billing/usage` |
| `GET` | `/api/v1/billing/models` |

Проверки тарифов и баланса выполняются через Entitlements Engine; тарифные значения и коммерческие параметры не фиксируются этим API draft. Внешний payment adapter на локальных проверках заменяется fake.

## 9. Ограничения запросов

Часть 4 требует ограничить размер тела, длину текста, количество вложений и частоту запросов. Числа пока не согласованы:

| Ограничение | Значение |
|---|---|
| Максимальный JSON body | TBD |
| Максимальная длина сообщения | TBD |
| Максимум вложений на сообщение | TBD |
| Максимальный размер файла, расширения и MIME types | TBD; upload endpoints до согласования лимитов не реализовывать |
| Максимальный `limit` для cursor pagination | TBD |
| Rate limits по account/IP и отдельным endpoint groups | TBD |

Значения должны быть отражены в OpenAPI, проверяться до записи в БД и возвращать единый error response.

## 10. Проверки и критерии приёмки

### Локально, до переноса на сервер

- Router tests с fake dependencies.
- Sync GET и короткий CRUD не создают background job.
- Повтор webhook/provider event не создаёт duplicate Inbox event/job.
- `202` возвращается только после фиксации Inbox event и job.
- Worker получает задачу и обрабатывает её без обращения к реальным MAX/Telegram/LLM/Qdrant.
- Проверки ролей, scopes, account/project isolation и assigned-dialog access.
- Cursor pagination, ошибки и OpenAPI contract tests.
- Проверка WebSocket authentication, tenant-scoped subscription, событий и reconnect behaviour.

### Первый smoke test на сервере

- Используется копия Clover, перенесённая на сервер.
- Реальный MAX webhook; если MAX подключить невозможно — Telegram.
- Входящее событие сохраняется в Inbox и получает durable job.
- Worker проходит доступный workflow с fake LLM/Qdrant и фиксирует результат/ошибку без потери события.
- Фактическая отправка ответа в мессенджер не требуется для первого smoke test.
- Перед применением миграций — backup и проверка на копии; запрещено удалять или пересоздавать рабочую БД тестовым teardown-кодом.

## 11. TBD перед утверждением

1. Окончательные route paths и DTO для agent, channel, catalog, knowledge, leads и analytics.
2. Матрица scopes × роли × endpoint.
3. Формат error codes и cross-tenant response (`403` или маскирующий `404`).
4. Формат `sub` от Bitrix: текущий verifier требует UUID, контракт identity описывает внешний immutable ID.
5. Idempotency-Key: формат, длина (текущая колонка — максимум 200 символов) и retention period.
6. Численные body/text/upload/rate limits.
7. Server-to-server credential: формат, выдача, хранение, rotation/revocation.
8. Provider-specific MAX handshake/signature; Telegram fallback setup; максимальная длина event ID (текущая колонка — 200 символов).
9. WebSocket ticket TTL, heartbeat, replay/reconnect/cursor semantics.
10. Публичные статусы операций и безопасное представление worker failures.
11. Модель/миграция для `operator` и `system` message roles.
12. Readiness contract: текущий `/ready` зависит от PostgreSQL и Qdrant; нужно определить, как показывать готовность core API при использовании fake Qdrant.
13. Коммерческие billing значения и платёжные периоды остаются TBD; API не задаёт цены и grants.

## 12. Источники и связанные ограничения

- `BACKEND_MVP_TZ.md`, Part 4: versioned API, management/dialog endpoints, real-time, idempotency, limits, `API_CONTRACT.md`, async acknowledgement and operation status.
- `BACKEND_MVP_TZ.md`, Part 2.5, item 11: billing endpoint inventory.
- `BACKEND_MVP_TZ.md`, session lifecycle sections: API/webhook persists Inbox + durable job; only Dialog Worker owns session lifecycle.
- `BACKEND_MVP_TZ.md`, backlog: public project archive/delete/restore endpoints belong to Part 4; real provider and server scheduler work requires deployment credentials/server.
