# Внешние интеграции ИИ-продавца

> **Источник истины по составу и архитектуре MVP-1 — [BACKEND_MVP_TZ.md](./BACKEND_MVP_TZ.md).** При расхождении применяются требования ТЗ; этот документ не утверждает новые функции, публичные API или архитектурные решения.
>
> В MVP-1 каналом является только MAX. Ниже сохранена подробная спецификация MAX; разделы Ozon, Avito, Bitrix24, примеры других каналов и расширения общего адаптерного фреймворка — материалы для последующих этапов, не объём MVP-1. MVP-1 использует один выбранный LLM provider за расширяемым внутренним интерфейсом: provider routing, автоматический fallback и выбор поставщика на уровне проекта/навыка не входят в MVP-1. Конкретный поставщик LLM пока не утверждён.
>
> Каталог MVP-1 импортируется из CSV/XLSX одного авторитетного источника. YML, API/живые источники, расписание синхронизации, несколько источников и их приоритеты относятся к последующим этапам. Заявки MVP-1 хранятся только внутри сервиса и создаются только после явного подтверждения покупателя; передача в CRM не входит в MVP-1.
>
> Архитектурный контекст MVP-1: модульный монолит непосредственно на Linux под `systemd`, без контейнеризации. PostgreSQL — источник истины для business state, Inbox, durable jobs и Outbox; Redis необязателен, worker продолжает polling PostgreSQL при его недоступности; Qdrant хранит производный индекс.
>
> До AI-чата требуется обязательная форма ПДн. ПДн не передаются LLM: данные формы и прямые идентификаторы покупателя исключаются из контекста модели; свободные сообщения очищаются от ПДн до обычного хранения и отправки модели. Если надёжность очистки не подтверждена, исходное сообщение модели не отправляется. MAX payload и примеры ниже не являются разрешением передавать такие данные модели.
>
> Детали endpoint, схем, лимитов, тарифов и поведения любого provider, включая MAX и LLM, требуется перепроверить по его актуальной официальной документации перед реализацией. Provider-specific спецификации ниже сохраняются как технические справочные материалы, а не как гарантия актуальности. Пути вида `/api/v1/...` и кодовые фрагменты — иллюстративные внутренние контракты; они сами по себе не утверждают публичный API для покупателей или сторонних клиентов. Публичный API управления/клиентский API определяется отдельно в согласованном API-контракте.
>
> Все входящие данные нормализуются в `UnifiedEvent`. Примеры внутри описаний каналов, помеченные как message event, иллюстрируют только message-specific payload; CRM-, lifecycle-, call-, status- и системные события остаются отдельными типами событий и не получают фиктивный пустой текст.

---

## 1. MAX Messenger

**Источник:** [API MAX](https://dev.max.ru/docs-api/), [OpenAPI Schema](https://github.com/max-messenger/api-schema/blob/main/schema.yaml)  
**Базовый URL:** `https://platform-api2.max.ru`  
**Авторизация:** `Authorization: <access_token>` (header)  
**Тип канала:** `max`  
**Статус:** ✅ Единственный канал MVP-1; реализация и фактические API-ограничения проверяются по официальной документации MAX.

---

### 1.1. Решения и границы MVP-1

| Принцип ядра | Реализация для MAX |
|--------------|-------------------|
| **F1** Account → Project | Канал привязан к `project_id`. Один проект = один бот MAX (один токен). Несколько проектов = несколько ботов. |
| **F3** Data Providers | Адаптер MAX не обращается к БД напрямую. Диалоговый движок использует `ProductDataProvider`, `OrderProvider`, `CustomerDataProvider`. |
| **F6** Function Registry | MVP-1: поиск/получение товара из импортированного CSV/XLSX-каталога, ответы по базе знаний, создание внутренней заявки после явного подтверждения и передача оператору. Статус внешнего заказа, запись на консультацию и сбор контактов как отдельная функция здесь не утверждаются. |
| **F7** Unified Response + Channel Capabilities | Единый формат ответа ядра → конвертер в формат MAX (текст, inline-клавиатуры, медиа, share-ссылки). |
| **LLM provider** | В MVP-1 один выбранный provider за расширяемым внутренним интерфейсом; конкретный поставщик не утверждён. Нет выбора provider на уровне проекта/навыка, маршрутизации или автоматического fallback между providers. |
| **F27** Skills (goal/flow) | Сценарии продаж реализуются как навыки: `goal` (свободный диалог с инструментами) или `flow` (детерминированные слоты). |

---

### 1.2. Схема взаимодействия

```
┌─────────────┐     Webhook (HTTPS POST)      ┌──────────────────────┐
│   MAX User  │ ─────────────────────────────► │  MAX Bot (platform)  │
└─────────────┘                                └──────────┬───────────┘
                                                          │
                                              Webhook URL (наш эндпоинт)
                                                          │
                                                          ▼
┌─────────────┐     Unified Response          ┌──────────────────────┐
│   MAX User  │ ◄──────────────────────────── │  AI Seller Core      │
└─────────────┘   (converted to MAX format)   │  ┌────────────────┐  │
                                              │  │ MAX Adapter    │  │
                                              │  │ - receive      │  │
                                              │  │ - convert      │  │
                                              │  │ - send         │  │
                                              │  └───────┬────────┘  │
                                              │          │           │
                                              │  ┌───────┴────────┐  │
                                              │  │ Dialog Engine  │  │
                                              │  │ - Skills       │  │
                                              │  │ - Rules        │  │
                                              │  │ - Functions    │  │
                                              │  └───────┬────────┘  │
                                              │          │           │
                                              │  ┌───────┴────────┐  │
                                              │  │ LLM Provider  │  │
                                              │  │ Data Providers │  │
                                              │  └────────────────┘  │
                                              └──────────────────────┘
```

**Режим получения обновлений MVP-1:** **Webhook (рекомендован MAX)**
- В MVP-1 применяется webhook; получение входящих обновлений через MAX long polling не является рабочим режимом MVP-1.
- Long Polling (`/updates`) — только для разработки, не для продакшена
- Webhook и Long Polling **нельзя использовать одновременно**
- Сервер вебхука **обязательно на порту 443** (HTTPS)
- Повторная доставка при ошибке: экспоненциальный бэкофф до 8 часов

---

### 1.3. Ключевые эндпоинты MAX API (используемые адаптером)

| Назначение | Метод | Путь | Описание |
|------------|-------|------|----------|
| **Инфо о боте** | `GET` | `/me` | Проверка токена, получение `user_id` бота |
| **Отправка сообщения** | `POST` | `/messages` | Основной метод ответа пользователю |
| **Редактирование сообщения** | `PUT` | `/messages` | `message_id` в query, тело `NewMessageBody` |
| **Удаление сообщения** | `DELETE` | `/messages` | `message_id` в query |
| **Ответ на callback** | `POST` | `/answers` | `callback_id` в query, тело `CallbackAnswer` |
| **Загрузка медиа** | `POST` | `/uploads?type=image\|video\|audio\|file` | Получение URL для загрузки → загрузка файла → токен для вложения |
| **Подписка на вебхук** | `POST` | `/subscriptions` | `url`, `secret`, `update_types` |
| **Отписка** | `DELETE` | `/subscriptions` | `url` в query |
| **Получение обновлений (long polling)** | `GET` | `/updates` | `limit`, `timeout`, `marker`, `types` |
| **Инфо о чате** | `GET` | `/chats/{chatId}` | Тип чата, участники, статус |
| **Действие (typing...)** | `POST` | `/chats/{chatId}/actions` | `typing_on`, `sending_photo`, `mark_seen` |

---

### 1.4. Типы обновлений (Webhook payload)

Адаптер должен обрабатывать следующие `update_type` (из OpenAPI `Update` discriminator):

| Тип | Когда приходит | Важные поля | Действие адаптера |
|-----|----------------|-------------|-------------------|
| `message_created` | Новое сообщение от пользователя | `message` (Message), `user_locale` | Создать/найти сессию, передать в Dialog Engine |
| `message_callback` | Нажатие inline-кнопки | `callback` (Callback), `message`, `user_locale` | Найти сессию, передать `callback.payload` в Dialog Engine как вызов функции/переход |
| `bot_started` | Пользователь нажал «Старт» / перешел по deep-link | `chat_id`, `user`, `payload`, `user_locale` | Инициализировать новую сессию, `payload` → контекст (utm, start-параметры) |
| `bot_stopped` | Пользователь остановил бота | `chat_id`, `user`, `user_locale` | Пометить сессию как остановленную, не отвечать |
| `message_edited` | Сообщение отредактировано | `message` | (Опционально) переобработать |
| `message_removed` | Сообщение удалено | `message_id`, `chat_id`, `user_id` | (Опционально) логировать |
| `user_added` / `user_removed` | Участники в групповом чате | `chat_id`, `user`, `is_channel` | Для групповых чатов (если бот админ с `read_all_messages`) |
| `bot_added` / `bot_removed` | Бот добавлен/удалён из чата | `chat_id`, `user`, `is_channel` | Регистрация/дерегистрация канала в проекте |

**Не запускают Dialog Runtime в MVP-1:** `comment_*`, `dialog_cleared`, `dialog_removed`, `dialog_muted`, `dialog_unmuted`, `chat_title_changed`, `bot_admin_permissions_changed`. Если подписка/endpoint получает такие updates, адаптер нормализует их в `conversation_lifecycle`/`channel_lifecycle` или `unsupported`, сохраняет/учитывает согласно политике аудита и не превращает в сообщение с пустым текстом. Изменение прав бота должно обновлять health/capability state, если оно доступно через API.

---

### 1.5. Форматы сообщений (маппинг Unified ↔ MAX)

#### 1.5.1. Входящее сообщение (MAX → Unified)

```python
# MAX MessageCreatedUpdate.message.body
{
    "mid": "mid.xxx",
    "seq": 123,
    "text": "Привет, ищу кроссовки",
    "attachments": [
        {"type": "image", "payload": {"photo_id": 1, "token": "...", "url": "..."}},
        {"type": "contact", "payload": {"vcf_info": "...", "max_info": {...}}},
        {"type": "location", "latitude": 55.7, "longitude": 37.6},
        {"type": "inline_keyboard", "payload": {"buttons": [[{"type": "callback", "text": "Купить", "payload": "buy_123"}]]}}
    ],
    "markup": [{"type": "link", "from": 0, "length": 10, "url": "https://..."}]
}

# → UnifiedEvent (message event; message-specific payload)
{
    "session_id": "chat_id:user_id",  # или dialog_id
    "channel_id": "max:bot_user_id",
    "user_id": "max:user_id",
    "message_id": "mid.xxx",
    "text": "Привет, ищу кроссовки",
    "attachments": [
        {"type": "image", "url": "..."},
        {"type": "contact", "data": {...}},
        {"type": "location", "lat": 55.7, "lon": 37.6}
    ],
    "callback_payload": None,  # заполняется только для message_callback
    "metadata": {
        "user_locale": "ru_RU",
        "seq": 123,
        "timestamp": 1737500130100
    }
}
```

#### 1.5.2. Исходящее сообщение (Unified → MAX)

```python
# Unified Response (от Dialog Engine)
{
    "text": "Нашел кроссовки Nike Air Max",
    "cards": [
        {
            "title": "Nike Air Max 270",
            "description": "Размер 42, черный, в наличии",
            "image_url": "https://...",
            "price": "8990 ₽",
            "buttons": [
                {"type": "callback", "text": "В корзину", "payload": "add_to_cart:sku_123"},
                {"type": "link", "text": "Подробнее", "url": "https://shop.ru/product/123"}
            ]
        }
    ],
    "keyboard": [
        [{"type": "callback", "text": "Другие модели", "payload": "show_more"}],
        [{"type": "message", "text": "Статус заказа"}]
    ],
    "parse_mode": "markdown"
}

# → MAX NewMessageBody
{
    "text": "Нашел кроссовки Nike Air Max",
    "format": "markdown",
    "attachments": [
        {
            "type": "share",
            "payload": {"url": "https://shop.ru/product/123", "token": "..."},
            "title": "Nike Air Max 270",
            "description": "Размер 42, черный, в наличии — 8990 ₽",
            "image_url": "https://..."
        },
        {
            "type": "inline_keyboard",
            "payload": {
                "buttons": [
                    [{"type": "callback", "text": "В корзину", "payload": "add_to_cart:sku_123"}],
                    [{"type": "link", "text": "Подробнее", "url": "https://shop.ru/product/123"}],
                    [{"type": "callback", "text": "Другие модели", "payload": "show_more"}],
                    [{"type": "message", "text": "Статус заказа"}]
                ]
            }
        }
    ],
    "notify": true
}
```

**Важные нюансы конвертации:**
- **Карточки товаров** → `ShareAttachment` (link preview с картинкой, заголовком, описанием) + `InlineKeyboardAttachment` с кнопками
- **Inline-клавиатуры** → `InlineKeyboardAttachment` (массив массивов кнопок)
- **Кнопки типа `message`** → отправляют текст от имени пользователя (как quick reply)
- **Кнопки типа `callback`** → приходят в `message_callback` с `callback.payload`
- **Медиа** — 3 шага: 1) `POST /uploads?type=...` → URL, 2) загрузка файла на URL, 3) токен из ответа → `PhotoAttachmentRequest` / `VideoAttachmentRequest` и т.д.
- **Форматирование** — `format: "markdown"` (MAX-flavored) или `"html"`. Поддерживается: `*italic*`, `**bold**`, `~~strike~~`, `++underline++`, `` `code` ``, `^^highlight^^`, `> quote`, `[link](url)`, `# header`

---

### 1.6. Модель канала в БД (таблица `channels`)

```sql
-- Соответствует core-architecture.md сущность channels
CREATE TABLE channels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id),
    type VARCHAR(32) NOT NULL,  -- 'max'
    name VARCHAR(255),          -- название для админки
    credentials JSONB NOT NULL, -- зашифрованные: {"bot_token": "...", "webhook_secret": "..."}
    inbound_mode VARCHAR(16) NOT NULL DEFAULT 'webhook', -- 'webhook' | 'long_polling'
    health_state VARCHAR(24) NOT NULL DEFAULT 'requires_setup', -- healthy|degraded|failed|requires_setup|disabled
    failure_domain VARCHAR(32),                          -- credentials|permissions|subscription|provider_outage|rate_limit|configuration|unknown
    last_error TEXT,
    capabilities JSONB NOT NULL DEFAULT '{}',            -- см. ChannelCapabilities
    webhook_url VARCHAR(500),                            -- зарегистрированный URL
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Индексы
CREATE INDEX idx_channels_project ON channels(project_id);
CREATE INDEX idx_channels_type_health ON channels(type, health_state);
```

**Пример `capabilities` для MAX:**
```json
{
    "max_message_length": 4000,
    "supports_inline_keyboards": true,
    "supports_callback_buttons": true,
    "supports_share_attachments": true,
    "supports_media": ["image", "video", "audio", "file", "sticker", "contact", "location"],
    "supports_formatting": ["markdown", "html"],
    "supports_typing_indicator": true,
    "supports_message_edit": true,
    "supports_message_delete": true,
    "supports_webhook": true,
    "supports_long_polling": true,
    "webhook_requires_443": true,
    "max_buttons_per_row": 5,
    "max_keyboard_rows": 10,
    "callback_payload_max_length": 1024,
    "deep_link_payload_max_length": 128
}
```

---

### 1.7. Сессии и идентификация пользователей

| MAX сущность | Ядро (Unified) | Примечание |
|--------------|----------------|------------|
| `chat_id` (dialog) | `session_id` = `max:dialog:{chat_id}` | Диалог 1-на-1 |
| `chat_id` (chat/channel) | `session_id` = `max:chat:{chat_id}:{user_id}` | Групповой чат — сессия на пользователя |
| `user.user_id` | `customer_id` = `max:{user_id}` | Глобальный идентификатор покупателя |
| `user.username` | `customer_variables.username` | Может быть null |
| `user.first_name` / `last_name` | `customer_variables.first_name`, `last_name` | |
| `payload` из `bot_started` | `dialog_variables.start_payload`, `utm_*` | Deep-link параметры |

**Customer Identities (таблица `customer_identities`):**
```sql
INSERT INTO customer_identities (customer_id, channel_type, external_id)
VALUES ('max:12345', 'max', '12345');
```

---

### 1.8. Функции MAX и границы MVP-1 (реестр функций F6)

MVP-1 разрешает модели только функции сценария: поиск/получение товара из импортированного каталога, ответ по базе знаний, создание внутренней заявки **только после явного подтверждения покупателя** и передачу оператору. Заявка сохраняется в PostgreSQL и доступна авторизованным пользователям проекта; интеграционной отправки в CRM нет.

| Функция | Описание | Параметры | Статус / требования |
|---------|----------|-----------|---------------------|
| `search_products` | Поиск товаров по запросу/фильтрам | `query`, `category`, `price_min`, `price_max`, `attributes`, `limit` | MVP-1: каталог из одного авторитетного CSV/XLSX-источника |
| `get_product` | Карточка товара по ID/SKU | `product_id` \| `sku` | MVP-1: только импортированный каталог |
| `answer_from_knowledge` | Ответ по базе знаний | Поиск по базе знаний проекта | MVP-1 |
| `create_internal_request` | Создать внутреннюю заявку | Данные заявки и явное подтверждение покупателя | MVP-1: только локальное сохранение; не отправлять в CRM |
| `handoff_to_operator` | Эскалация к оператору | `reason`, `summary` | MVP-1: настроенная передача оператору |
| `compare_products`, `check_stock` | Сравнение / отдельная проверка остатков | По товарам/складу | Не входят в утверждённый набор функций MVP-1; не включать без отдельного согласования в ТЗ |
| `get_order_status`, `get_customer_orders`, `book_appointment`, `collect_contacts` | Внешний заказ, история заказов, запись и сбор контактов | Детали зависят от будущей интеграции | Не часть утверждённого набора функций MVP-1; эта справочная запись не подразумевает сбор контактов или соответствующие подключения |

**Пример callback payload** (разрешённые функции ограничиваются набором MVP-1):
```
"payload": "fn:search_products:{\"query\":\"кроссовки\",\"limit\":5}"
"payload": "fn:get_product:{\"sku\":\"NIKE-AM-270-42\"}"
```

Адаптер парсит `fn:<function_name>:<json_args>` и передаёт вызов реестру разрешённых функций. Неизвестные/неразрешённые функции отклоняются. Сам вызов функции или нажатие callback не является подтверждением заявки: создание разрешено только после явно выраженного подтверждения покупателя, проверенного в состоянии диалога.

**ПДн и MAX payload:** вложения `contact`, текстовые контакты, имена и другие прямые идентификаторы могут содержать ПДн. Они не поступают в LLM. Свободный текст очищается от ПДн до обычного хранения и отправки модели; если надёжность очистки не подтверждена, исходное сообщение модели не отправляется. Обязательная форма ПДн должна быть пройдена до начала AI-чата.

---

### 1.9. Настройка вебхука (Deployment)

**Иллюстративный внутренний webhook ingress:** `POST /api/v1/webhooks/max/{channel_id}`. Это адрес приёма вебхука MAX, а не публичный API для покупателей; итоговые маршруты API фиксируются отдельно.

```python
# Псевдокод обработчика: фиксирует событие до ACK; обработку выполняет worker
@router.post("/webhooks/max/{channel_id}")
async def max_webhook(channel_id: str, request: Request):
    # 1. Верификация подписи (header X-Max-Bot-Api-Secret)
    secret = channel.credentials["webhook_secret"]
    if not verify_signature(request.headers.get("X-Max-Bot-Api-Secret"), secret):
        raise HTTPException(401, "Invalid signature")
    
    # 2. Парсинг обновления
    try:
        payload = await request.json()
    except JSONDecodeError:
        raise HTTPException(400, "Invalid JSON payload")
    update = parse_update(payload)  # pydantic model с discriminator
    
    # 3. Нормализовать поддерживаемые MAX update types (не только сообщения).
    # Свободный текст очистить от ПДн до обычного хранения; raw payload не передавать LLM.
    events = max_adapter.parse_update(update, channel_id)
    # Inbox + background_jobs фиксируются транзакционно и дедуплицируются
    await inbox_service.persist_events_and_jobs(channel, events)
    return max_adapter.webhook_ack()
```

**Регистрация вебхука при создании канала:**
```python
async def setup_max_webhook(channel: Channel, base_url: str):
    webhook_url = f"{base_url}/api/v1/webhooks/max/{channel.id}"
    secret = generate_secret(32)
    
    async with MaxClient(channel.credentials["bot_token"]) as client:
        await client.subscribe(webhook_url, secret, update_types=[
            "message_created",
            "message_callback",
            "bot_started",
            "bot_stopped",
            "message_edited",
            "message_removed"
        ])
    
    # Сохраняем secret в credentials (зашифрованный)
    channel.credentials["webhook_secret"] = secret
    channel.webhook_url = webhook_url
    await channel.save()
```

---

### 1.10. Обработка медиа (загрузка файлов)

**Отправка изображения/видео/аудио/файла от бота:**

```python
async def send_media(max_client: MaxClient, chat_id: int, file_path: Path, media_type: str):
    # 1. Получить URL для загрузки
    upload_url = await max_client.get_upload_url(media_type)
    
    # 2. Загрузить файл
    token = await max_client.upload_file(upload_url["url"], file_path)
    
    # 3. Отправить сообщение с вложением
    attachment_request = {
        "type": media_type,  # image, video, audio, file
        "payload": {"token": token}
    }
    await max_client.send_message(chat_id=chat_id, attachments=[attachment_request])
```

**Получение медиа от пользователя:**
- В `message_created` приходят `attachments` с `payload.url` (готовый CDN URL)
- Для видео — можно вызвать `GET /videos/{videoToken}` для получения прямых ссылок по качествам

---

### 1.11. Обработка ошибок и ретраи

| HTTP код | Действие |
|----------|----------|
| `200` | Успех |
| `400` | Неверный запрос — логировать, не ретраить (баг в коде) |
| `401` | Ошибка/истечение токена — обновить токен, если поддерживается, и повторить один раз. При окончательном отказе пометить подключение `failed` с `failure_domain=credentials` |
| `403` | Бот не имеет доступа к чату (пользователь заблокировал/удалил) — пометить сессию `stopped` |
| `404` | Ресурс не найден (сообщение удалено) — игнорировать |
| `429` | Rate limit — экспоненциальный бэкофф (начинать с 1с, макс 60с) |
| `500` / `502` / `503` / timeout | Временный сбой provider — сохранять исходящие в Outbox, ограниченно повторять; канал `degraded` с `failure_domain=provider_outage` |

Ошибки доставки исходящего сообщения не обрабатываются в webhook-процессе и не запускают синхронные повторы. Проверка статуса канала учитывает успешные запросы и накопление ошибок; временная проблема платформы отображается как деградация, а не как неверный токен.

**Rate limits MAX:** не задокументированы публично, рекомендуется не более 30 req/s на бота.

---

### 1.12. Тестирование интеграции

**Локальная разработка (ngrok / cloudflare tunnel):**
```bash
# Туннель на localhost:8000
ngrok http 8000
# → https://abc123.ngrok-free.app

# В .env
WEBHOOK_BASE_URL=https://abc123.ngrok-free.app
```

**Тестовый бот MAX:**
1. Создать бота на [business.max.ru](https://business.max.ru/self)
2. Получить токен в настройках бота
3. Добавить в `.env` проекта: `MAX_BOT_TOKEN=...`
4. Запустить ядро → вебхук зарегистрируется автоматически

**Автотесты (F28):**
- Симулятор пользователя MAX (мокает вебхук payloads)
- Проверка конвертации Unified ↔ MAX форматов
- Проверка обработки всех типов обновлений
- Проверка отправки медиа, клавиатур, карточек

---

### 1.13. Чек-лист готовности к продакшену

- [ ] Адаптер реализован и покрыт тестами
- [ ] Вебхук зарегистрирован на HTTPS 443 порту
- [ ] Секрет вебхука хранится зашифрованным
- [ ] Обработаны все критические `update_type`
- [ ] Реализован конвертер Unified ↔ MAX (текст, клавиатуры, карточки, медиа)
- [ ] Настроены ретраи с бэкоффом для 429/503
- [ ] Логирование входящих/исходящих сообщений (без ПДн, F17)
- [ ] Метрики: latency, error rate, messages in/out
- [ ] Документация для админов: как подключить бота в ЛК

---

### 1.14. Известные ограничения MAX (на момент написания)

1. **Групповые чаты:** бот получает `message_created` только если он админ с правом `read_all_messages`
2. **Каналы:** бот может писать в канал, но не читать сообщения (только комментарии к постам)
3. **Deep linking:** payload до 128 символов, передается в `bot_started.payload`
4. **Файлы:** видео/аудио обрабатываются асинхронно — токен может быть не готов сразу после загрузки
5. **Markdown:** свой диалект (например, `^^highlight^^` для подсветки)
6. **Нет поддержки:** reply keyboards (только inline), альбомов (несколько фото в одном сообщении), опросов
7. **Только юрлицы/ИП/самозанятые резиденты РФ** могут создавать ботов

---

### 1.15. Ссылки для команды

- [Документация API MAX](https://dev.max.ru/docs-api/)
- [OpenAPI Schema (YAML)](https://raw.githubusercontent.com/max-messenger/api-schema/main/schema.yaml)
- [JS SDK](https://github.com/max-messenger/max-bot-api-client-ts)
- [Go SDK](https://github.com/max-messenger/max-bot-api-client-go)
- [Платформа для бизнеса](https://business.max.ru/self)

---

## Приложение А: Примеры payloads (для тестов)

### A.1. message_created (текст)
```json
{
  "update_type": "message_created",
  "timestamp": 1737500130100,
  "message": {
    "sender": {"user_id": 12345, "first_name": "Иван", "username": "ivan_123", "is_bot": false},
    "recipient": {"chat_type": "dialog", "chat_id": 987654321, "user_id": 12345},
    "timestamp": 1737500130100,
    "body": {
      "mid": "mid.abc123",
      "seq": 1,
      "text": "Привет, ищу кроссовки размер 42",
      "attachments": null,
      "markup": null
    }
  },
  "user_locale": "ru_RU"
}
```

### A.2. message_callback (нажатие кнопки)
```json
{
  "update_type": "message_callback",
  "timestamp": 1737500135000,
  "callback": {
    "timestamp": 1737500135000,
    "callback_id": "keyboard_123",
    "payload": "fn:search_products:{\"query\":\"кроссовки\",\"limit\":5}",
    "user": {"user_id": 12345, "first_name": "Иван", "username": "ivan_123", "is_bot": false}
  },
  "message": {
    "sender": {"user_id": 99999, "first_name": "Shop Bot", "is_bot": true},
    "recipient": {"chat_type": "dialog", "chat_id": 987654321, "user_id": 12345},
    "timestamp": 1737500130100,
    "body": {
      "mid": "mid.abc123",
      "seq": 1,
      "text": "Что ищете?",
      "attachments": [{"type": "inline_keyboard", "payload": {"buttons": [[{"type": "callback", "text": "Кроссовки", "payload": "fn:search_products:{\\\"query\\\":\\\"кроссовки\\\"}"}]}}]
    }
  },
  "user_locale": "ru_RU"
}
```

### A.3. bot_started (deep link)
```json
{
  "update_type": "bot_started",
  "timestamp": 1737500140000,
  "chat_id": 987654321,
  "user": {"user_id": 12345, "first_name": "Иван", "username": "ivan_123", "is_bot": false},
  "payload": "utm_source=max&utm_campaign=summer_sale&product_sku=NIKE-AM-270-42",
  "user_locale": "ru_RU"
}
```

---

## Приложение Б: Маппинг типов вложений

| Unified Type | MAX Attachment Type | Примечание |
|--------------|---------------------|------------|
| `text` | — | В `text` поля `NewMessageBody` |
| `image` | `image` | `PhotoAttachmentRequest` (url или token) |
| `video` | `video` | `VideoAttachmentRequest` (token после загрузки) |
| `audio` | `audio` | `AudioAttachmentRequest` (token, только одно вложение) |
| `file` | `file` | `FileAttachmentRequest` (token, только одно вложение) |
| `sticker` | `sticker` | `StickerAttachmentRequest` (code) |
| `contact` | `contact` | `ContactAttachmentRequest` (vcf или contact_id) |
| `location` | `location` | `LocationAttachmentRequest` (lat/lon) |
| `card` (product) | `share` + `inline_keyboard` | ShareAttachment для превью + кнопки |
| `keyboard` | `inline_keyboard` | Двумерный массив кнопок |
| `callback_button` | `callback` | payload до 1024 символов |
| `url_button` | `link` | url до 2048 символов |
| `contact_request_button` | `request_contact` | Возможность формата MAX, не функция MVP-1; не использовать для обхода обязательной формы ПДн |
| `location_request_button` | `request_geo_location` | Запрос геолокации |

---

---

## 2. Ozon Seller API (Chat)

> **Не MVP-1.** Справочная provider-specific спецификация для последующего этапа; API, тарифы, лимиты и примеры требуется заново проверить по официальной документации перед реализацией.

**Источник:** [Ozon Seller API Docs](https://docs.ozon.ru/api/seller/) — раздел Chat (Premium методы)  
**Базовый URL:** `https://api-seller.ozon.ru`  
**Авторизация:** Headers `Client-Id` (string) + `Api-Key` (string)  
**Тип канала:** `ozon_chat`  
**Статус:** ⏭️ Будущий этап (не MVP-1)
**Важно:** Доступно **только для продавцов с подпиской Premium Plus или Premium Pro**

---

### 2.1. Архитектурное решение (согласно core-architecture.md)

| Принцип ядра | Реализация для Ozon Chat |
|--------------|-------------------------|
| **F1** Account → Project | Один проект = один `Client-Id` (аккаунт продавца). Несколько магазинов/аккаунтов = несколько проектов. |
| **F3** Data Providers | Адаптер не обращается к БД напрямую. Диалоговый движок использует `ProductDataProvider` (каталог Озон), `OrderProvider` (заказы/отправления), `CustomerDataProvider` (покупатель). |
| **F6** Function Registry | Функции: `get_order_status` (по posting_number), `get_product_info` (по SKU), `create_chat` (по posting_number), `send_message`. Условия: тариф Premium, схема FBS/FBO/rFBS. |
| **F7** Unified Response + Channel Capabilities | Единый формат ответа ядра → конвертер в формат Озон (plain text до 1000 символов, без форматирования, без кнопок/карточек). |
| **LLM provider** | В MVP-1 один выбранный provider за внутренним интерфейсом; этот раздел не означает выбора модели или маршрутизации на уровне проекта/навыка. |
| **F27** Skills (goal/flow) | Сценарии: «Уточнение адреса/модели» (flow со слотами), «Статус заказа» (goal с функцией), «Ответ на частые вопросы» (goal с KB). |

**Ключевое отличие от мессенджеров:** Ozon Chat — это **не полноценный мессенджер**, а сервисный канал привязанный к **отправлениям (posting)**. Бот не может писать первым произвольно — только в окне SLA (48ч для FBO, 72ч для FBS/rFBS после оплаты/доставки) или в ответ на сообщение покупателя.

---

### 2.2. Схема взаимодействия

```
┌─────────────┐     HTTPS POST (webhook/polling)  ┌──────────────────────┐
│  Покупатель │ ◄──────────────────────────────── │  Ozon Platform       │
│  (в приложе)│     Push-уведомления (webhook)     │  (Chat API)          │
└─────────────┘                                    └──────────┬───────────┘
                                                               │
                                               Webhook URL (наш эндпоинт)
                                                               │
                                                               ▼
┌─────────────┐     Unified Response          ┌──────────────────────┐
│  Покупатель │ ◄──────────────────────────── │  AI Seller Core      │
└─────────────┘   (converted to Ozon format)  │  ┌────────────────┐  │
                                              │  │ Ozon Adapter   │  │
                                              │  │ - receive      │  │
                                              │  │ - convert      │  │
                                              │  │ - send         │  │
                                              │  └───────┬────────┘  │
                                              │          │           │
                                              │  ┌───────┴────────┐  │
                                              │  │ Dialog Engine  │  │
                                              │  │ - Skills       │  │
                                              │  │ - Rules        │  │
                                              │  │ - Functions    │  │
                                              │  └───────┬────────┘  │
                                              │          │           │
                                              │  ┌───────┴────────┐  │
                                              │  │ LLM Interface │  │
                                              │  │ Data Providers │  │
                                              │  │ (Ozon API)     │  │
                                              │  └────────────────┘  │
                                              └──────────────────────┘
```

**Режим получения обновлений:** **Push-уведомления (Webhook)** — рекомендуемый  
- Настройка вебхука в ЛК Озон или через API (есть метод регистрации)
- События: `TYPE_NEW_MESSAGE`, `TYPE_UPDATE_MESSAGE`, `TYPE_MESSAGE_READ`, `TYPE_CHAT_CLOSED`
- Также доступен Long Polling через `GET /v3/chat/list` + `GET /v3/chat/get` (не для продакшена)

---

### 2.3. Ключевые эндпоинты Ozon Chat API

| Назначение | Метод | Путь | Описание |
|------------|-------|------|----------|
| **Отправить сообщение** | `POST` | `/v1/chat/send/message` | Текст 1-1000 символов, plain text. Требует `chat_id` |
| **Создать чат** | `POST` | `/v1/chat/start` | По `posting_number` (только FBS/rFBS, в течение 72ч после оплаты/доставки) |
| **Отметить прочитанными** | `POST` | `/v2/chat/read` | `chat_id` + `from_message_id` (uint64) |
| **Список чатов** | `POST` | `/v3/chat/list` | Фильтры: `chat_type="Buyer_Seller"`, пагинация |
| **Получить чат** | `POST` | `/v3/chat/get` | Детали чата по `chat_id` |
| **История сообщений** | `POST` | `/v3/chat/get_messages` | По `chat_id`, лимит, маркер |
| **Шаблоны** | `POST` | `/v2/chat/get_templates` | Список шаблонов быстрых ответов |
| **Отправить шаблон** | `POST` | `/v2/chat/send_template` | По `chat_id` + `template_id` |
| **Статус сообщения** | `POST` | `/v2/chat/get_message_status` | По `chat_id` + `message_id` |
| **Участники чата** | `POST` | `/v2/chat/get_chat_participants` | Список участников |

**Headers для всех запросов:**
```
Client-Id: <string>      # Идентификатор клиента (числовой ID продавца)
Api-Key: <string>        # API-ключ из ЛК Озон
Content-Type: application/json
```

---

### 2.4. Push-уведомления (Webhook events)

Адаптер должен обрабатывать следующие `message_type`:

| Тип | Когда приходит | Важные поля | Действие адаптера |
|-----|----------------|-------------|-------------------|
| `TYPE_NEW_MESSAGE` | Новое сообщение от покупателя/поддержки | `chat_id`, `chat_type`, `message_id`, `created_at`, `user{id,type}`, `data[]` (Markdown), `seller_id` | Найти/создать сессию, передать в Dialog Engine |
| `TYPE_UPDATE_MESSAGE` | Сообщение отредактировано | + `updated_at` | (Опционально) переобработать |
| `TYPE_MESSAGE_READ` | Сообщение прочитано покупателем | `last_read_message_id` | Обновить статус доставки/прочтения |
| `TYPE_CHAT_CLOSED` | Чат закрыт | `user{id,type}` | Пометить сессию как закрытую, не отвечать |

**Важно:** `chat_type` фильтруем — интересует только `Buyer_Seller` и `Buyer_Seller_Select`. Остальные (Support, Notifications) — игнорируем или логируем.

---

### 2.5. Форматы сообщений (маппинг Unified ↔ Ozon)

#### 2.5.1. Входящее сообщение (Ozon → Unified)

```python
# Ozon Push: TYPE_NEW_MESSAGE
{
    "message_type": "TYPE_NEW_MESSAGE",
    "chat_id": "b646d975-0c9c-4872-9f41-8b1e57181063",
    "chat_type": "Buyer_Seller",
    "message_id": "3000000000817031942",
    "created_at": "2022-07-18T20:58:04.528Z",
    "user": {"id": "115568", "type": "Customer"},
    "data": ["Текст сообщения в Markdown"],
    "seller_id": "7"
}

# → UnifiedEvent (message event; message-specific payload)
{
    "session_id": "ozon:chat:b646d975-0c9c-4872-9f41-8b1e57181063",
    "channel_id": "ozon_chat:client_12345",
    "customer_id": "ozon:customer:115568",
    "message_id": "3000000000817031942",
    "text": "Текст сообщения в Markdown",
    "attachments": [],
    "callback_data": None,
    "metadata": {
        "chat_type": "Buyer_Seller",
        "user_type": "Customer",
        "created_at": "2022-07-18T20:58:04.528Z",
        "seller_id": "7"
    }
}
```

#### 2.5.2. Исходящее сообщение (Unified → Ozon)

```python
# Unified Response (от Dialog Engine)
{
    "text": "Ваш заказ 123456 в статусе 'На упаковке'. Ожидайте курьера.",
    "cards": [],           # Ozon не поддерживает
    "keyboard": None,      # Ozon не поддерживает inline-кнопки
    "parse_mode": "plain"  # Только plain text
}

# → Ozon Request Body (POST /v1/chat/send/message)
{
    "chat_id": "b646d975-0c9c-4872-9f41-8b1e57181063",
    "text": "Ваш заказ 123456 в статусе 'На упаковке'. Ожидайте курьера."
}

# Response
{"result": "success"}
```

**Критические ограничения Озон Chat:**
- ❌ **Нет форматирования** (Markdown/HTML) — только plain text
- ❌ **Нет кнопок/клавиатур** — только текст
- ❌ **Нет медиа** (картинки, файлы, стикеры)
- ❌ **Нет карточек товаров** — только текст
- ✅ **Только текст** до 1000 символов
- ✅ **Markdown в входящих** — `data[]` приходит в Markdown, парсим в plain text

---

### 2.6. Модель канала в БД (таблица `channels`)

```sql
CREATE TABLE channels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id),
    type VARCHAR(32) NOT NULL,  -- 'ozon_chat'
    name VARCHAR(255),          -- "Ozon Chat: Магазин №1"
    credentials JSONB NOT NULL, -- зашифрованные: {"client_id": "12345", "api_key": "..."}
    inbound_mode VARCHAR(16) NOT NULL DEFAULT 'webhook', -- 'webhook' | 'polling'
    health_state VARCHAR(24) NOT NULL DEFAULT 'requires_setup',
    failure_domain VARCHAR(32),
    last_error TEXT,
    capabilities JSONB NOT NULL DEFAULT '{}',
    webhook_url VARCHAR(500),
    webhook_secret VARCHAR(256),  # для верификации подписи (есть в Озон)
    ozon_seller_id BIGINT,        # числовой ID продавца (из seller_id в push)
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_channels_project ON channels(project_id);
CREATE INDEX idx_channels_type_health ON channels(type, health_state);
```

**Пример `capabilities` для Ozon Chat:**
```json
{
    "max_message_length": 1000,
    "supports_formatting": false,
    "supports_inline_keyboards": false,
    "supports_media": [],
    "supports_cards": false,
    "supports_templates": true,
    "supports_typing_indicator": false,
    "supports_message_edit": false,
    "supports_message_delete": false,
    "supports_webhook": true,
    "supports_polling": true,
    "sla_rules": {
        "FBO": {"reply_window_hours": 48, "trigger": "last_customer_message"},
        "FBS": {"reply_window_hours": 72, "trigger": "payment_or_delivery"},
        "rFBS": {"reply_window_hours": 72, "trigger": "payment_or_delivery"}
    },
    "chat_types_supported": ["Buyer_Seller", "Buyer_Seller_Select"],
    "requires_premium_subscription": ["Premium Plus", "Premium Pro"]
}
```

---

### 2.7. Сессии и идентификация

| Ozon сущность | Ядро (Unified) | Примечание |
|---------------|----------------|------------|
| `chat_id` (UUID) | `session_id` = `ozon:chat:{chat_id}` | 1 чат = 1 сессия |
| `user.id` (покупатель) | `customer_id` = `ozon:customer:{user.id}` | Глобальный ID покупателя |
| `chat_type` | `dialog_variables.chat_type` | `Buyer_Seller` / `Buyer_Seller_Select` |
| `posting_number` | `dialog_variables.posting_number` | Привязка к отправлению (для SLA) |
| `seller_id` | `channel.ozon_seller_id` | Числовой ID продавца |

**Customer Identities:**
```sql
INSERT INTO customer_identities (customer_id, channel_type, external_id)
VALUES ('ozon:customer:115568', 'ozon_chat', '115568');
```

**SLA логика (критично):**
- Сессия имеет `paused_until` / `expires_at` на основе правил:
  - FBO: 48 часов после последнего сообщения покупателя
  - FBS/rFBS: 72 часа после оплаты/доставки, потом 48 часов после последнего сообщения
- Вне окна — бот **не может** отправить сообщение (вернёт 409 Conflict)
- Диалоговый движок должен проверять SLA перед отправкой

---

### 2.8. Функции, доступные в Ozon Chat (реестр функций F6)

| Функция | Описание | Параметры | Требования |
|---------|----------|-----------|------------|
| `send_message` | Ответ покупателю в чате | `chat_id`, `text` | Активная сессия, в окне SLA |
| `create_chat` | Инициировать чат по отправлению | `posting_number` | FBS/rFBS, 72ч после оплаты/доставки |
| `mark_read` | Отметить сообщения прочитанными | `chat_id`, `from_message_id` | - |
| `get_chat_list` | Список чатов с покупателями | `filter: chat_type=Buyer_Seller`, `limit`, `offset` | Premium подписка |
| `get_chat` | Детали чата | `chat_id` | - |
| `get_messages` | История сообщений | `chat_id`, `limit`, `marker` | - |
| `get_order_status` | Статус заказа/отправления | `posting_number` \| `order_number` | `OrderProvider` (Ozon API) |
| `get_product_info` | Инфо о товаре по SKU | `sku` | `ProductDataProvider` (Ozon API) |
| `get_templates` | Шаблоны быстрых ответов | - | Premium подписка |
| `send_template` | Отправить шаблон | `chat_id`, `template_id` | - |

---

### 2.9. Настройка вебхука (Deployment)

**Эндпоинт в ядре:** `POST /api/v1/webhooks/ozon_chat/{channel_id}`

```python
@router.post("/webhooks/ozon_chat/{channel_id}")
async def ozon_chat_webhook(channel_id: str, request: Request):
    # 1. Верификация подписи (Ozon отправляет заголовок X-Ozon-Signature)
    secret = channel.credentials.get("webhook_secret")  # настраивается в ЛК Озон
    if not verify_ozon_signature(request.headers.get("X-Ozon-Signature"), secret, await request.body()):
        raise HTTPException(401, "Invalid signature")
    
    # 2. Парсинг уведомления
    try:
        payload = await request.json()
    except JSONDecodeError:
        raise HTTPException(400, "Invalid JSON payload")
    # 3. Нормализовать поддерживаемые message/status/chat lifecycle events.
    # Не отбрасывать только потому, что событие не является текстовым сообщением.
    events = ozon_adapter.parse_inbound(payload, dict(request.headers))
    await inbox_service.persist_events_and_jobs(channel, events)
    return ozon_adapter.webhook_ack()
```

**Регистрация вебхука в Озон:**
- В ЛК Озон: Настройки → API → Push-уведомления → Добавить URL
- Или через API (есть метод регистрации вебхука в разделе "Пуш-уведомления")

---

### 2.10. Обработка ошибок и ретраи

| HTTP код | Действие |
|----------|----------|
| `200` | Успех |
| `400` | Неверный параметр — логировать, не ретраить |
| `401` | Ошибка/отзыв `Client-Id` или `Api-Key` — `health_state=failed`, `failure_domain=credentials`; запросить проверку/замену ключа. Не классифицировать как истечение тарифа |
| `403` | Отдельно диагностировать недостаток прав (`permissions`) и недоступность Chat API из-за тарифа/подписки (`subscription`), только если ответ API/проверка аккаунта это подтверждает. Не выводить причину только из факта `403`; при неразличимом ответе — `unknown` с диагностикой и повторной проверкой |
| `404` | Чат/сообщение не найдено — игнорировать |
| `409` | **Конфликт: вне окна SLA** — сессия истекла, не ретраить, логировать как "SLA expired" |
| `429` | Rate limit — экспоненциальный бэкофф (начинать с 1с) |
| `500` / `502` / `503` / timeout | Временная недоступность Ozon — ограниченный retry с backoff, `health_state=degraded`, `failure_domain=provider_outage`; не требовать замены ключа |

**Диагностика подключения Ozon:** чат доступен только при требуемом тарифе Premium Plus/Premium Pro (по текущим сведениям этого документа). Поэтому health-check и обработчик ошибок должны различать подтверждённый недостаток подписки, неверные credentials, недостаточные права и временный сбой платформы. Для тарифа показывать действие «проверить/подключить требуемую подписку», для credentials — «проверить Client-Id и Api-Key». Если API не позволяет надёжно определить причину, показать «причина не подтверждена» и не выдавать ошибку тарифа как факт. Условия подписки и соответствие кодов ошибок перед production сверять с актуальной официальной документацией Ozon.

**Rate limits Озон:** не публично задокументированы для Chat API. Рекомендуется консервативно: 10 req/s на аккаунт.

---

### 2.11. Тестирование интеграции

**Локальная разработка:**
```bash
ngrok http 8000
# → https://abc123.ngrok-free.app
WEBHOOK_BASE_URL=https://abc123.ngrok-free.app
```

**Тестовый аккаунт Озон:**
- Нужен продавец с Premium Plus/Pro подпиской
- Создать тестовое отправление (FBS) → открыть чат → писать сообщения
- Проверить push-уведомления на ngrok URL

**Автотесты (F28):**
- Моки push-событий (`TYPE_NEW_MESSAGE`, `TYPE_CHAT_CLOSED`, etc.)
- Проверка SLA логики (FBO/FBS/rFBS окна)
- Проверка передачи лимита 1000 символов до генерации и серверной проверки результата; превышение не обрезается молча, а обрабатывается политикой валидации/повторной генерации
- Проверка 409 Conflict handling

---

### 2.12. Чек-лист готовности к продакшену

- [ ] Адаптер реализован и покрыт тестами
- [ ] Вебхук зарегистрирован в ЛК Озон (HTTPS 443)
- [ ] Секреты (Client-Id, Api-Key, Webhook Secret) хранятся зашифрованными
- [ ] Обработаны все критические `message_type`
- [ ] Реализован конвертер Unified ↔ Ozon (plain text, без форматирования)
- [ ] **SLA логика реализована** (проверка окна перед отправкой, 409 handling)
- [ ] Фильтр `chat_type` (только Buyer_Seller)
- [ ] Логирование входящих/исходящих (без ПДн)
- [ ] Метрики: latency, error rate, SLA violations, messages in/out
- [ ] Документация для админов: как подключить Premium аккаунт, где взять ключи

---

### 2.13. Известные ограничения Ozon Chat (на момент написания)

1. **Только Premium Plus/Pro** — базовые тарифы не имеют доступа
2. **Привязка к отправлениям** — чат существует только в контексте заказа (posting_number)
3. **SLA окна** — бот не может писать первым произвольно
4. **Только plain text** — нет Markdown, кнопок, медиа, карточек
5. **Нет проактивных рассылок** — только ответы в существующих чатах
6. **Лимиты длины** — 1000 символов на сообщение
7. **Push-уведомления** — нужно настраивать в ЛК, нет API для управления подпиской (на момент проверки)
8. **Типы чатов** — 8 типов, нам нужны только 2 (`Buyer_Seller`, `Buyer_Seller_Select`)

---

### 2.14. Ссылки для команды

- [Ozon Seller API Docs](https://docs.ozon.ru/api/seller/) — раздел Chat (Premium)
- [Push-уведомления](https://docs.ozon.ru/api/seller/#tag/Push-уведомления) — настройка вебхуков
- [Postman коллекция](https://docs.ozon.ru/api/seller/) — в разделе "Консоль" можно экспортировать

---

### Приложение А: Примеры payloads (для тестов)

#### A.1. TYPE_NEW_MESSAGE (входящее)
```json
{
  "message_type": "TYPE_NEW_MESSAGE",
  "chat_id": "b646d975-0c9c-4872-9f41-8b1e57181063",
  "chat_type": "Buyer_Seller",
  "message_id": "3000000000817031942",
  "created_at": "2022-07-18T20:58:04.528Z",
  "user": {"id": "115568", "type": "Customer"},
  "data": ["Здравствуйте, когда придет заказ?"],
  "seller_id": "7"
}
```

#### A.2. TYPE_CHAT_CLOSED
```json
{
  "message_type": "TYPE_CHAT_CLOSED",
  "chat_id": "b646d975-0c9c-4872-9f41-8b1e57181063",
  "chat_type": "Buyer_Seller",
  "user": {"id": "115568", "type": "Customer"},
  "seller_id": "7"
}
```

#### A.3. Отправка сообщения (запрос)
```json
POST /v1/chat/send/message
Headers: Client-Id: 12345, Api-Key: xxx
{
  "chat_id": "b646d975-0c9c-4872-9f41-8b1e57181063",
  "text": "Ваш заказ в пути, ожидайте доставку завтра."
}
```

#### A.4. Создание чата (запрос)
```json
POST /v1/chat/start
Headers: Client-Id: 12345, Api-Key: xxx
{
  "posting_number": "47873153-0052-1"
}
```

---

### Приложение Б: Маппинг типов вложений

| Unified Type | Ozon Chat | Примечание |
|--------------|-----------|------------|
| `text` | `text` (plain) | Только plain text, до 1000 символов |
| `image` | ❌ | Не поддерживается |
| `video` | ❌ | Не поддерживается |
| `audio` | ❌ | Не поддерживается |
| `file` | ❌ | Не поддерживается |
| `card` | ❌ | Не поддерживается |
| `keyboard` | ❌ | Не поддерживается |
| `template` | `template_id` | Через `/v2/chat/send_template` |

---

---

## 3. Avito Messenger API

> **Не MVP-1.** Справочная provider-specific спецификация для последующего этапа; API, тарифы, лимиты и примеры требуется заново проверить по официальной документации перед реализацией.

**Источник:** [Avito Developers](https://www.avito.ru/developers/api-catalog) — раздел Messenger, OpenAPI 3.0 (swagger.json)  
**Базовый URL:** `https://api.avito.ru`  
**Авторизация:** OAuth 2.0 — два режима:
- **Client Credentials** — для доступа к своим данным (access token от `https://api.avito.ru/token`)
- **Authorization Code** — для доступа к данным других пользователей (через `https://avito.ru/oauth`)
**Скопи:** `messenger:read`, `messenger:write`  
**Тип канала:** `avito_messenger`  
**Статус:** ⏭️ Будущий этап (не MVP-1)
**Важно:** В Товарах и Работе доступен только на подписке **«Максимальный»**. В Услугах — на **«Расширенный»** и **«Максимальный»**.

---

### 3.1. Архитектурное решение (согласно core-architecture.md)

| Принцип ядра | Реализация для Avito Messenger |
|--------------|-------------------------------|
| **F1** Account → Project | Один проект = один `user_id` (аккаунт продавца на Авито). Несколько аккаунтов = несколько проектов. |
| **F3** Data Providers | Адаптер не обращается к БД напрямую. Диалоговый движок использует `ProductDataProvider` (каталог Авито — объявления), `OrderProvider` (нет нативных заказов, но есть сделки/брони), `CustomerDataProvider` (профиль покупателя). |
| **F4** Unified Catalog Pipeline | Каталог Авито = объявления (items). Синхронизация через `items:info` scope. Одно объявление = один товар в нашем каталоге. |
| **F5** Product Model | Авито-объявление: `id`, `title`, `price_string`, `images[]`, `url`, `status_id`, `user_id` (автор). Характеристики — в параметрах объявления. |
| **F6** Function Registry | Функции: `search_items` (поиск объявлений), `get_item` (детали объявления), `send_message`, `send_image`, `mark_read`, `get_chats`, `get_messages`. Условия: подписка «Максимальный»/«Расширенный», scope `messenger:write`. |
| **F7** Unified Response + Channel Capabilities | Единый формат ответа ядра → конвертер в формат Авито (text, image, item/link preview). Поддерживает: текст ≤1000 симв., изображения, ссылки на объявления, геолокацию, голосовые. |
| **LLM provider** | В MVP-1 один выбранный provider за внутренним интерфейсом; этот раздел не означает выбора модели или маршрутизации на уровне проекта/навыка. |
| **F27** Skills (goal/flow) | Сценарии: «Подбор товара по параметрам» (goal с `search_items`), «Ответ на вопросы по объявлению» (flow со слотами), «Показ каталога» (goal). |

**Ключевая особенность:** Avito Messenger — это чат **в контексте объявления (u2i)** или профиля (u2u). Бот продавца общается с покупателем **по конкретному товару**. Контекст объявления (`item_id`, `title`, `price`, `images`) приходит в `Chat.context` — это идеально подходит под **F30 (контекст страницы/корзины)** из core-architecture.md.

---

### 3.2. Схема взаимодействия

```
┌─────────────┐     HTTPS POST (webhook)        ┌──────────────────────┐
│  Покупатель │ ◄────────────────────────────── │  Avito Platform      │
│  (в приложе)│     Push-уведомления (webhook)   │  (Messenger API)     │
└─────────────┘                                    └──────────┬───────────┘
                                                               │
                                               Webhook URL (наш эндпоинт)
                                                               │
                                                               ▼
┌─────────────┐     Unified Response          ┌──────────────────────┐
│  Покупатель │ ◄──────────────────────────── │  AI Seller Core      │
└─────────────┘   (converted to Avito format) │  ┌────────────────┐  │
                                              │  │ Avito Adapter  │  │
                                              │  │ - receive      │  │
                                              │  │ - convert      │  │
                                              │  │ - send         │  │
                                              │  └───────┬────────┘  │
                                              │          │           │
                                              │  ┌───────┴────────┐  │
                                              │  │ Dialog Engine  │  │
                                              │  │ - Skills       │  │
                                              │  │ - Rules        │  │
                                              │  │ - Functions    │  │
                                              │  └───────┬────────┘  │
                                              │          │           │
                                              │  ┌───────┴────────┐  │
                                              │  │ LLM Interface │  │
                                              │  │ Data Providers │  │
                                              │  │ (Avito API)    │  │
                                              │  └────────────────┘  │
                                              └──────────────────────┘
```

**Режим получения обновлений:** **Webhook v3** (рекомендован)  
- `POST /messenger/v3/webhook` — регистрация URL
- События: новое сообщение (`type: "message"`), прочтение, удаление
- Требования: URL должен отвечать 200 OK в течение **2 секунд** (timeout)
- Также доступен Long Polling: `GET /messenger/v3/accounts/{user_id}/chats/{chat_id}/messages` (не для продакшена)

---

### 3.3. Ключевые эндпоинты Avito Messenger API

| Назначение | Метод | Путь | Описание |
|------------|-------|------|----------|
| **Отправить текст** | `POST` | `/messenger/v1/accounts/{user_id}/chats/{chat_id}/messages` | `{"message": {"text": "..."}, "type": "text"}`, макс 1000 симв. |
| **Отправить изображение** | `POST` | `/messenger/v1/accounts/{user_id}/chats/{chat_id}/messages/image` | `{"image_id": "..."}` — предварительно загрузить через `/uploadImages` |
| **Удалить сообщение** | `POST` | `/messenger/v1/accounts/{user_id}/chats/{chat_id}/messages/{message_id}` | В течение 1 часа после отправки |
| **Прочитать чат** | `POST` | `/messenger/v1/accounts/{user_id}/chats/{chat_id}/read` | Помечает сообщения прочитанными |
| **Список чатов** | `GET` | `/messenger/v2/accounts/{user_id}/chats` | Фильтры: `chat_types` (u2i/u2u/a2u), `item_ids`, `unread_only`, `limit`, `offset` |
| **Получить чат** | `GET` | `/messenger/v2/accounts/{user_id}/chats/{chat_id}` | Детали чата + последнее сообщение + **контекст объявления** |
| **История сообщений** | `GET` | `/messenger/v3/accounts/{user_id}/chats/{chat_id}/messages` | `limit`, `offset`. **Не помечает прочитанными!** |
| **Загрузить изображение** | `POST` | `/messenger/v1/accounts/{user_id}/uploadImages` | `multipart/form-data`, макс 24 МБ, 75 Мпкс, JPEG/HEIC/GIF/BMP/PNG |
| **Получить голосовое** | `GET` | `/messenger/v1/accounts/{user_id}/getVoiceFiles` | По `voice_ids[]`, ссылка живет 1 час, кодек Opus в MP4 |
| **Вебхук: подписаться** | `POST` | `/messenger/v3/webhook` | `{"url": "https://..."}` |
| **Вебхук: отписаться** | `POST` | `/messenger/v1/webhook/unsubscribe` | `{"url": "https://..."}` |
| **Вебхук: список** | `POST` | `/messenger/v1/subscriptions` | Список активных подписок |
| **Блокировка пользователя** | `POST` | `/messenger/v2/accounts/{user_id}/blacklist` | `users[]` с `user_id`, `context.item_id`, `reason_id` (1-спам, 2-мошенничество, 3-оскорбления, 4-другое) |

**Headers для всех запросов:**
```
Authorization: Bearer <access_token>
Content-Type: application/json
```

**Rate Limits:** заголовки ответа `X-RateLimit-Limit` (запросов в минуту), `X-RateLimit-Remaining`.

---

### 3.4. Webhook Events (v3)

Адаптер получает `POST` на зарегистрированный URL с телом:

```json
{
  "id": "abc",
  "payload": {
    "type": "message",
    "value": {
      "author_id": 123,
      "chat_id": "abc",
      "chat_type": "u2i",
      "content": {"text": "привет!"},
      "created": 123,
      "id": "abc",
      "item_id": 123,
      "published_at": "1973-03-04T13:33:09Z",
      "read": 123,
      "type": "text",
      "user_id": 123
    }
  },
  "timestamp": 123,
  "version": "v1.1"
}
```

**Типы входящих сообщений (`payload.value.type`):**
- `text` — текст (`content.text`)
- `image` — изображение (`content.image.sizes` — словарь размеров → URL)
- `item` — ссылка на объявление (`content.item`: `title`, `price_string`, `image_url`, `item_url`)
- `link` — ссылка с превью (`content.link.preview`: `domain`, `description`, `images`)
- `location` — геолокация (`content.location`: `lat`, `lon`, `text`, `title`, `kind`)
- `voice` — голосовое (`content.voice.voice_id` → получить файл через `/getVoiceFiles`)
- `call` — звонок (`content.call`: `status: "missed"`, `target_user_id`)
- `system` — системное (`content.flow_id` — ID чат-бота)
- `deleted` — удалённое

**Типы чатов (`chat_type`):**
- `u2i` — чат по объявлению (основной для продаж) — **есть `item_id` и контекст объявления**
- `u2u` — чат между пользователями (по профилю)
- `a2u` — чат с поддержкой Авито

---

### 3.5. Форматы сообщений (маппинг Unified ↔ Avito)

#### 3.5.1. Входящее сообщение (Avito → Unified)

```python
# Avito Webhook: payload.value
{
    "author_id": 115568,
    "chat_id": "b646d975-0c9c-4872-9f41-8b1e57181063",
    "chat_type": "u2i",
    "content": {"text": "Здравствуйте, есть в наличии?"},
    "created": 1737500130,
    "id": "msg_123",
    "item_id": 1768287444,
    "published_at": "2025-01-22T10:00:00Z",
    "type": "text",
    "user_id": 7376
}

# → UnifiedEvent (message event; message-specific payload)
{
    "session_id": "avito:chat:b646d975-0c9c-4872-9f41-8b1e57181063",
    "channel_id": "avito_messenger:user_7376",
    "customer_id": "avito:customer:115568",
    "message_id": "msg_123",
    "text": "Здравствуйте, есть в наличии?",
    "attachments": [],
    "callback_data": None,
    "metadata": {
        "chat_type": "u2i",
        "item_id": 1768287444,
        "author_id": 115568,
        "created_at": 1737500130,
        "published_at": "2025-01-22T10:00:00Z"
    }
}
```

**Важно:** При `chat_type=u2i` в `Chat.context` (из `GET /chats/{chat_id}`) приходит полная инфа об объявлении:
```json
{
  "context": {
    "type": "item",
    "value": {
      "id": 1768287444,
      "title": "Mazda 3 2008",
      "price_string": "300 000 ₽",
      "images": {"count": 4, "main": {"140x105": "https://..."}},
      "status_id": 10,
      "url": "https://avito.ru/moskva/avtomobili/mazda_3_2008_1768287444",
      "user_id": 141906442
    }
  }
}
```
→ Это **контекст страницы (F30)** — бот знает, какой товар смотрит покупатель!

#### 3.5.2. Исходящее сообщение (Unified → Avito)

```python
# Unified Response (от Dialog Engine)
{
    "text": "Да, Mazda 3 2008 в наличии. Цена 300 000 ₽. Хотите посмотреть фото?",
    "cards": [
        {
            "title": "Mazda 3 2008",
            "description": "300 000 ₽, пробег 120 000 км",
            "image_url": "https://...",
            "price": "300 000 ₽",
            "item_url": "https://avito.ru/.../1768287444"
        }
    ],
    "keyboard": None,
    "parse_mode": "plain"
}

# → Avito: отправка текста
POST /messenger/v1/accounts/7376/chats/b646d975-0c9c-4872-9f41-8b1e57181063/messages
{
    "message": {"text": "Да, Mazda 3 2008 в наличии. Цена 300 000 ₽. Хотите посмотреть фото?"},
    "type": "text"
}

# → Avito: отправка изображения (если нужно показать фото)
# 1. Загрузить изображение
POST /messenger/v1/accounts/7376/uploadImages (multipart/form-data)
# Response: {"image_id_123": {"1280x960": "https://...", "140x105": "https://...", ...}}

# 2. Отправить сообщение с изображением
POST /messenger/v1/accounts/7376/chats/.../messages/image
{"image_id": "image_id_123"}

# → Avito: отправка ссылки на объявление (item preview)
# Бот может отправить сообщение типа "item" — но API поддерживает только text и image.
# Workaround: отправить текст с ссылкой на объявление (авто-превью сработает на клиенте).
```

**Поддерживаемые исходящие типы:**
- ✅ `text` — до 1000 символов
- ✅ `image` — через предзагрузку (`uploadImages` → `image_id`)
- ❌ `item` — **нет API для отправки item-сообщения** (только входящие)
- ❌ `link` — нет API (но текст с URL рендерится клиентом как превью)
- ❌ `location`, `voice`, `call` — нет API для отправки

---

### 3.6. Модель канала в БД (таблица `channels`)

```sql
CREATE TABLE channels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id),
    type VARCHAR(32) NOT NULL,  -- 'avito_messenger'
    name VARCHAR(255),          -- "Avito Messenger: Магазин Авто"
    credentials JSONB NOT NULL, -- зашифрованные: {"access_token": "...", "refresh_token": "...", "token_expires_at": 1234567890, "client_id": "...", "client_secret": "..."}
    inbound_mode VARCHAR(16) NOT NULL DEFAULT 'webhook', -- 'webhook' | 'polling'
    health_state VARCHAR(24) NOT NULL DEFAULT 'requires_setup',
    failure_domain VARCHAR(32),
    last_error TEXT,
    capabilities JSONB NOT NULL DEFAULT '{}',
    webhook_url VARCHAR(500),
    webhook_version VARCHAR(16) DEFAULT 'v3',
    avito_user_id BIGINT,       -- числовой ID аккаунта продавца (user_id в API)
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_channels_project ON channels(project_id);
CREATE INDEX idx_channels_type_health ON channels(type, health_state);
```

**Пример `capabilities` для Avito Messenger:**
```json
{
    "max_message_length": 1000,
    "supports_formatting": false,
    "supports_outgoing_text": true,
    "supports_outgoing_image": true,
    "supports_outgoing_item": false,
    "supports_outgoing_link_preview": true,
    "supports_incoming_text": true,
    "supports_incoming_image": true,
    "supports_incoming_item": true,
    "supports_incoming_link": true,
    "supports_incoming_location": true,
    "supports_incoming_voice": true,
    "supports_incoming_call": true,
    "supports_incoming_system": true,
    "supports_typing_indicator": false,
    "supports_message_edit": false,
    "supports_message_delete": true,
    "delete_window_minutes": 60,
    "supports_webhook": true,
    "supports_polling": true,
    "webhook_timeout_seconds": 2,
    "rate_limit_headers": ["X-RateLimit-Limit", "X-RateLimit-Remaining"],
    "chat_types_supported": ["u2i", "u2u"],
    "requires_subscription": ["Максимальный", "Расширенный (Услуги)"],
    "oauth_scopes": ["messenger:read", "messenger:write"],
    "context_support": {
        "item_context_in_chat": true,
        "item_fields": ["id", "title", "price_string", "images", "url", "status_id"]
    }
}
```

---

### 3.7. Сессии и идентификация

| Avito сущность | Ядро (Unified) | Примечание |
|----------------|----------------|------------|
| `chat_id` (string) | `session_id` = `avito:chat:{chat_id}` | 1 чат = 1 сессия |
| `user_id` (покупатель, int64) | `customer_id` = `avito:customer:{user_id}` | Глобальный ID покупателя |
| `chat_type` | `dialog_variables.chat_type` | `u2i` / `u2u` / `a2u` |
| `item_id` (int) | `dialog_variables.item_id` | **Ключевой контекст** — привязка к объявлению |
| `avito_user_id` (продавец) | `channel.avito_user_id` | Числовой ID аккаунта продавца |

**Customer Identities:**
```sql
INSERT INTO customer_identities (customer_id, channel_type, external_id)
VALUES ('avito:customer:115568', 'avito_messenger', '115568');
```

**Контекст объявления (F30):**
При получении чата (`GET /chats/{chat_id}`) в `context.value` приходит полная карточка товара. Это сохраняется в `dialog_variables.item_context` и доступно навыкам/функциям для точных ответов (цена, фото, характеристики — **не придуманные, а реальные из Авито**).

---

### 3.8. Функции, доступные в Avito Messenger (реестр функций F6)

| Функция | Описание | Параметры | Требования |
|---------|----------|-----------|------------|
| `send_message` | Ответ покупателю в чате | `chat_id`, `text` | `messenger:write`, активная сессия |
| `send_image` | Отправить фото товара | `chat_id`, `image_bytes` → `uploadImages` → `image_id` | `messenger:write` |
| `mark_read` | Отметить чат прочитанным | `chat_id` | `messenger:read` |
| `get_chats` | Список чатов с покупателями | `chat_types[]`, `item_ids[]`, `unread_only`, `limit`, `offset` | `messenger:read` |
| `get_chat` | Детали чата + контекст объявления | `chat_id` | `messenger:read` |
| `get_messages` | История сообщений | `chat_id`, `limit`, `offset` | `messenger:read` |
| `get_voice_file` | Ссылка на голосовое сообщение | `voice_ids[]` | `messenger:read` |
| `upload_image` | Загрузка изображения | `file_bytes` | `messenger:write` |
| `search_items` | Поиск объявлений продавца | `query`, `category`, `params`, `limit` | `items:info` scope, отдельный API |
| `get_item` | Детали объявления по ID | `item_id` | `items:info` scope |
| `block_user` | Блокировка спамера | `user_id`, `item_id`, `reason_id` | `messenger:write` |

**Важно:** Поиск товаров (`search_items`, `get_item`) — это **отдельный API Авито** (Autoload/Items API), не Messenger API. Нужен scope `items:info` / `items:write`. Адаптер должен интегрироваться с обоими API.

---

### 3.9. OAuth 2.0 — получение и обновление токенов

**Client Credentials (для своего аккаунта):**
```python
# 1. Получить токен
POST https://api.avito.ru/token
Headers: Content-Type: application/x-www-form-urlencoded
Body: grant_type=client_credentials&client_id=XXX&client_secret=YYY

# Response:
{
  "access_token": "xxx",
  "token_type": "Bearer",
  "expires_in": 3600
}

# 2. Использовать в запросах
Authorization: Bearer xxx
```

**Authorization Code (для чужих аккаунтов — приложения):**
1. Редирект пользователя на `https://avito.ru/oauth?client_id=XXX&response_type=code&scope=messenger:read+messenger:write+items:info&redirect_uri=...`
2. Пользователь подтверждает → редирект на `redirect_uri?code=...`
3. Обмен `code` на токен: `POST /token` с `grant_type=authorization_code`
4. Сохранить `access_token`, `refresh_token`, `expires_in`
5. При истечении — `grant_type=refresh_token`

**Токены хранятся в `channels.credentials` зашифрованными.**

---

### 3.10. Настройка вебхука (Deployment)

**Эндпоинт в ядре:** `POST /api/v1/webhooks/avito_messenger/{channel_id}`

```python
@router.post("/webhooks/avito_messenger/{channel_id}")
async def avito_webhook(channel_id: str, request: Request):
    # 1. Проверка источника и принадлежности события этому подключению
    try:
        payload = await request.json()
    except JSONDecodeError:
        raise HTTPException(400, "Invalid JSON payload")
    if not avito_adapter.verify_webhook_payload(channel, payload):
        raise HTTPException(401, "Webhook source mismatch")
    
    # 2. Нормализовать событие любого поддерживаемого типа (включая missed call,
    # system и lifecycle); только тип message направляется в Dialog Runtime.
    events = avito_adapter.parse_inbound(payload, dict(request.headers))
    await inbox_service.persist_events_and_jobs(channel, events)
    return avito_adapter.webhook_ack()
```

Avito webhook в этой интеграционной схеме не считается подписанным криптографически, если это не подтверждено актуальным API. Проверка user/account ID — дополнительная проверка маршрутизации, но не полноценная подпись. Любое событие, не прошедшее проверку подключения/формата, не должно молча считаться доверенным.

**Регистрация вебхука при создании канала:**
```python
async def setup_avito_webhook(channel: Channel, base_url: str):
    webhook_url = f"{base_url}/api/v1/webhooks/avito_messenger/{channel.id}"
    
    async with AvitoClient(channel.credentials["access_token"]) as client:
        await client.subscribe_webhook_v3(webhook_url)
    
    channel.webhook_url = webhook_url
    channel.webhook_version = "v3"
    await channel.save()
```

---

### 3.11. Обработка ошибок и ретраи

| HTTP код | Действие |
|----------|----------|
| `200` / `201` | Успех |
| `400` | Validation error — логировать, не ретраить |
| `401` | Токен истёк/невалиден — обновить через refresh_token, ретраить 1 раз |
| `402` | Payment required — подписка не позволяет (если причина подтверждена) — `health_state=requires_setup`, `failure_domain=subscription` |
| `403` | Нет прав/scope — `health_state=failed`, `failure_domain=permissions`; не путать с истёкшим токеном |
| `404` | Чат/сообщение не найдено — игнорировать |
| `429` | Rate limit — читать `X-RateLimit-Remaining`, бэкофф |
| `500` / `502` / `503` / timeout | Временная ошибка provider — Outbox retry с backoff, `health_state=degraded`, `failure_domain=provider_outage` |

`402`/`403` трактуются согласно фактическому коду/телу официального API. Если подписка и права не различимы, причина остаётся `unknown`, а интерфейс не приписывает сбой тарифу без подтверждения.

**Rate limits:** не публично задокументированы точно. Заголовки `X-RateLimit-Limit`, `X-RateLimit-Remaining` в ответах. Рекомендуется консервативно: 30 req/min на аккаунт.

---

### 3.12. Тестирование интеграции

**Локальная разработка:**
```bash
ngrok http 8000
# → https://abc123.ngrok-free.app
WEBHOOK_BASE_URL=https://abc123.ngrok-free.app
```

**Тестовый аккаунт Авито:**
- Нужен аккаунт с подпиской «Максимальный» (Товары/Работа) или «Расширенный»/«Максимальный» (Услуги)
- Создать тестовое объявление → написать в чат с другого аккаунта
- Проверить push-уведомления на ngrok URL

**Автотесты (F28):**
- Моки webhook payloads (text, image, item, link, location, voice)
- Проверка преобразования message event в исходящие Avito payloads (text ≤1000, image upload flow)
- Проверка контекста объявления (`Chat.context.value`) в диалоге
- Проверка OAuth token refresh flow
- Проверка rate limit handling

---

### 3.13. Чек-лист готовности к продакшену

- [ ] Адаптер реализован и покрыт тестами
- [ ] OAuth 2.0 flow реализован (Client Credentials + Authorization Code + Refresh)
- [ ] Токены хранятся зашифрованными, авто-обновление при 401
- [ ] Вебхук v3 зарегистрирован, отвечает < 2s
- [ ] Обработаны заявленные типы входящих сообщений и событий (text, image, item, link, location, voice; call/system/deleted маршрутизируются как отдельные события)
- [ ] Реализован конвертер Unified → Avito (text, image)
- [ ] **Контекст объявления (F30)** извлекается и передаётся в Dialog Engine
- [ ] Интеграция с Items API для поиска товаров (`search_items`, `get_item`)
- [ ] Логирование входящих/исходящих (без ПДн)
- [ ] Метрики: latency, error rate, token refresh rate, messages in/out
- [ ] Документация для админов: как подключить аккаунт, где взять client_id/secret, какая подписка нужна

---

### 3.14. Известные ограничения Avito Messenger (на момент написания)

1. **Только платные подписки** — «Максимальный» (Товары/Работа) или «Расширенный»/«Максимальный» (Услуги)
2. **Исходящие сообщения:** только `text` (≤1000 симв.) и `image` (через upload). Нет отправки `item`, `link`, `location`, `voice`.
3. **Входящие данные:** сообщения могут содержать text, image, item, link, location и voice; call, system и deleted должны нормализоваться как отдельные события, а не как `MessageType`.
4. **Webhook timeout: 2 секунды** — endpoint должен только проверить, нормализовать и транзакционно сохранить событие с durable job, затем быстро вернуть ACK. LLM и отправка выполняются отдельными worker-процессами; in-memory background task не является гарантированной очередью.
5. **Голосовые:** Opus в MP4, ссылка живет 1 час.
6. **Удаление сообщений:** только в течение 1 часа.
7. **Rate limits:** через заголовки `X-RateLimit-*`, точные цифры не публикуются.
8. **Контекст объявления** доступен только в `GET /chats/{chat_id}` (не в вебхуке) — нужно кэшировать или дозапрашивать.
9. **Чаты типов `a2u` (с поддержкой Авито)** — игнорируем для продаж.

---

### 3.15. Ссылки для команды

- [Avito Developers Portal](https://www.avito.ru/developers/api-catalog)
- [Messenger API Docs](https://api.avito.ru/docs/api.html) (Swagger UI)
- [OAuth Documentation](https://www.avito.ru/developers/api-catalog/auth/documentation)
- [Условия использования API](https://www.avito.st/static/ims/openapi_apitermsofservicev1_common.pdf)

---

### Приложение А: Примеры payloads (для тестов)

#### A.1. Webhook: входящее текстовое сообщение (u2i)
```json
{
  "id": "msg_abc",
  "payload": {
    "type": "message",
    "value": {
      "author_id": 115568,
      "chat_id": "chat_xyz",
      "chat_type": "u2i",
      "content": {"text": "Здравствуйте, машина еще актуальна?"},
      "created": 1737500130,
      "id": "msg_abc",
      "item_id": 1768287444,
      "published_at": "2025-01-22T10:00:00Z",
      "type": "text",
      "user_id": 7376
    }
  },
  "timestamp": 1737500130,
  "version": "v1.1"
}
```

#### A.2. Webhook: входящее сообщение с объявлением (item)
```json
{
  "payload": {
    "type": "message",
    "value": {
      "author_id": 115568,
      "chat_id": "chat_xyz",
      "chat_type": "u2i",
      "content": {
        "item": {
          "title": "iPhone 13 Pro",
          "price_string": "75 000 ₽",
          "image_url": "https://...",
          "item_url": "https://avito.ru/.../12345"
        }
      },
      "created": 1737500130,
      "id": "msg_123",
      "item_id": 1768287444,
      "type": "item",
      "user_id": 7376
    }
  }
}
```

#### A.3. Отправка текста (запрос)
```json
POST /messenger/v1/accounts/7376/chats/chat_xyz/messages
Authorization: Bearer xxx
{
  "message": {"text": "Да, Mazda 3 2008 в наличии. Цена 300 000 ₽."},
  "type": "text"
}
```

#### A.4. Загрузка и отправка изображения
```bash
# 1. Загрузка
curl -X POST "https://api.avito.ru/messenger/v1/accounts/7376/uploadImages" \
  -H "Authorization: Bearer xxx" \
  -F "uploadfile[]=@photo.jpg"

# Response: {"img_123": {"1280x960": "https://...", "140x105": "https://...", ...}}

# 2. Отправка
curl -X POST "https://api.avito.ru/messenger/v1/accounts/7376/chats/chat_xyz/messages/image" \
  -H "Authorization: Bearer xxx" \
  -H "Content-Type: application/json" \
  -d '{"image_id": "img_123"}'
```

#### A.5. Контекст чата (GET /chats/{chat_id}) — ключевой для F30
```json
{
  "id": "chat_xyz",
  "created": 1737500000,
  "updated": 1737500130,
  "users": [{"id": 7376, "name": "АвтоСалон", ...}, {"id": 115568, "name": "Покупатель", ...}],
  "context": {
    "type": "item",
    "value": {
      "id": 1768287444,
      "title": "Mazda 3 2008",
      "price_string": "300 000 ₽",
      "images": {"count": 4, "main": {"140x105": "https://..."}},
      "status_id": 10,
      "url": "https://avito.ru/moskva/avtomobili/mazda_3_2008_1768287444",
      "user_id": 141906442
    }
  },
  "last_message": {...}
}
```

---

### 3.16. Полезные репозитории и инструменты для разработки

| Репозиторий | Описание | Что даёт |
|-------------|----------|----------|
| **[MissiaL/avito-api](https://github.com/MissiaL/avito-api)** ⭐ | Agent Skill для Avito Business API (OpenAPI 3.0, 248 путей, 27 разделов, снимок 03.10.2026) | • Полный `avito-api-openapi.json` (2.2 МБ)<br>• Per-section markdown доки в `references/sections/`<br>• CLI `lookup_endpoint.py` для поиска эндпоинтов без загрузки всего JSON<br>• OAuth2 хелпер `get_token.py`<br>• `SKILL.md` для ИИ-агентов (Claude Code, Cursor, Copilot, Goose) |
| **[AntonSHBK/avito_personal_template](https://github.com/AntonSHBK/avito_personal_template)** | Docker-шаблон с `openapi-generator` для генерации Python-клиента | • Готовая структура: `app/avito_api/auth`, `app/avito_api/ads`<br>• Jupyter notebooks для тестов<br>• `.env` конфигурация<br>• Пример генерации клиента через Docker |
| **[Sermelyan/Avito-chat-api](https://github.com/Sermelyan/Avito-chat-api)** | Наработки для чат-API Авито | (репозиторий приватный/недоступен на момент проверки) |

**Официальные SDK Авито (только для Рекламы/Ads API):**
- Python: `avito-ads-sdk-python`
- Go: `avito-ads-sdk-go`
- TypeScript: `avito-ads-sdk-ts`
- PHP: `avito-ads-sdk-php`

> **Важно:** Официальные SDK покрывают **только Ads API** (Реклама/Промо). Для Messenger API, Автозагрузки, Доставки, Работы и др. — используем сгенерированный клиент из OpenAPI (`MissiaL/avito-api/references/avito-api-openapi.json`).

**Рекомендуемый workflow для нашего проекта:**
```bash
# 1. Клонируем скилл как сабмодуль
git submodule add https://github.com/MissiaL/avito-api.git external/avito-api

# 2. Генерируем Python-клиент (openapi-generator или b24pysdk-подход)
# Вход: external/avito-api/references/avito-api-openapi.json
# Выход: ai_seller_core/adapters/avito/generated/

# 3. Используем lookup_endpoint.py для навигации по спеку
python external/avito-api/scripts/lookup_endpoint.py search "messenger"
```

---

### Приложение Б: Маппинг типов вложений

| Unified Type | Avito Incoming | Avito Outgoing | Примечание |
|--------------|----------------|----------------|------------|
| `text` | `text` (`content.text`) | `text` | ≤1000 символов |
| `image` | `image` (`content.image.sizes`) | `image` (via `uploadImages` + `image_id`) | Макс 24 МБ, 75 Мпкс |
| `card` (product) | `item` (`content.item`) | ❌ | Входящее: полная карточка. Исходящее: только текст с ссылкой |
| `link` | `link` (`content.link.preview`) | ❌ (auto-preview) | Текст с URL → клиент покажет превью |
| `location` | `location` (`content.location`) | ❌ | |
| `voice` | `voice` (`content.voice.voice_id`) | ❌ | Получить файл через `/getVoiceFiles` |
| `call` | `call` (`content.call`) | ❌ | Только missed calls |
| `system` | `system` (`content.flow_id`) | ❌ | Чат-боты Авито |
| `deleted` | `deleted` | ❌ | |

---

---

## 4. Bitrix24 REST API

> **Не MVP-1.** Вся CRM/IM/Tasks спецификация, включая создание лидов/сделок и передачу данных, является справочным материалом для будущих этапов. В MVP-1 внутренняя заявка остаётся в PostgreSQL, создаётся только после явного подтверждения покупателя и не отправляется в CRM.

**Источник:** [Официальная документация](https://apidocs.bitrix24.ru/), [GitHub репозиторий](https://github.com/bitrix-tools/b24-rest-docs) (Documentation as Code)  
**Базовый URL:** `https://{your-domain}.bitrix24.ru/rest/` (v1/v2) или `https://{your-domain}.bitrix24.ru/rest/api/` (REST 3.0)  
**Авторизация:** 
- **Входящий вебхук** — `https://domain.bitrix24.ru/rest/USER_ID/WEBHOOK_CODE/method`
- **OAuth 2.0** (локальное/тиражное приложение) — токен в параметре `auth`
**Скопи:** `crm`, `im`, `task`, `user`, `disk`, `catalog`, `sale`, `telephony`, `bizproc`, `landing`, `widget`, `messageservice`, `pay-system`, `cashbox`, `delivery`, `calendar`, `mail`, `note`, `iblock`, `department`, `event-log`, `timeman`, `telephony`, `forum`, `search`, `socialservices`, `sonet`, `tasks`, `vote`, `wiki`, `workflow`, `intranet`, `extranet`, `mobile`, `bitrix24`, `rest`  
**Тип канала:** `bitrix24_crm`, `bitrix24_im`, `bitrix24_tasks`  
**Статус:** ⏭️ Будущий этап (не MVP-1)
**SDK:** `b24pysdk` (Python), `b24gosdk` (Go), `b24jssdk` (JS/TS) — поддерживают REST 3.0

---

### 4.1. Архитектурное решение (согласно core-architecture.md)

| Принцип ядра | Реализация для Bitrix24 |
|--------------|------------------------|
| **F1** Account → Project | Один проект = один портал Битрикс24 (`member_id`). Несколько порталов = несколько проектов. |
| **F3** Data Providers | Адаптеры не обращаются к БД напрямую. Диалоговый движок использует `ProductDataProvider` (Торговый каталог), `OrderProvider` (Сделки/Заказы/Счета), `CustomerDataProvider` (Контакты/Компании/Лиды). |
| **F4** Unified Catalog Pipeline | Каталог Битрикс24 = Торговый каталог (разделы, товары, услуги, вариации, SKU, цены, склады). Синхронизация через `catalog.*` методы. |
| **F5** Product Model | Товар: `id`, `name`, `price`, `currency`, `measure`, `properties` (характеристики), `images`, `sections`, `variations` (SKU), `stores` (склады), `quantity`. |
| **F6** Function Registry | Функции: `crm.item.add/update/get/list` (универсальные), `crm.lead.add`, `crm.deal.add`, `crm.contact.add`, `crm.company.add`, `crm.timeline.logmessage.add`, `crm.item.productrow.set`, `tasks.task.add`, `im.message.add`. Условия: соответствующие скоупы, права пользователя. |
| **F7** Unified Response + Channel Capabilities | Единый формат ответа ядра → конвертер в формат Битрикс24 (JSON, поддержка HTML в полях, файлы через Disk). |
| **LLM provider** | В MVP-1 один выбранный provider за внутренним интерфейсом; этот раздел не означает выбора модели или маршрутизации на уровне проекта/навыка. |
| **F27** Skills (goal/flow) | Сценарии: «Создание сделки из чата» (flow), «Квалификация лида» (goal), «Автоматическое заполнение карточки» (flow с роботами). |
| **F30** Контекст страницы | При открытии карточки сделки/лида — контекст передаётся в навык (entityTypeId, entityId, stageId, поля). |

**Ключевая особенность:** Bitrix24 — это **CRM + Мессенджер (IM) + Задачи + Каталог + Автоматизация** в одной системе. Наш ИИ-продавец должен уметь:
1. **Читать/писать в CRM** (лиды, сделки, контакты, компании, товарные позиции)
2. **Общаться в чатах** (IM, открытые линии, чаты задач)
3. **Создавать задачи/дела** для менеджеров
4. **Писать в таймлайн** (лог-записи, комментарии)
4. **Реагировать на события** (вебхуки: `ONCRMLEADADD`, `ONCRMDEALUPDATE`, `ONIMMESSAGEADD`, `ONTASKADD`)

---

### 4.2. Схема взаимодействия

```
┌─────────────────┐     HTTPS POST (webhook)      ┌──────────────────────┐
│  Клиент/Менеджер │ ◄──────────────────────────── │  Bitrix24 Portal     │
│  (Чат/CRM/Задачи)│     События (webhook)         │  (REST API v1/v2/3)  │
└─────────────────┘                                └──────────┬───────────┘
                                                                │
                                                Webhook URL (наш эндпоинт)
                                                                │
                                                                ▼
┌─────────────────┐     Unified Response          ┌──────────────────────┐
│  Клиент/Менеджер │ ◄──────────────────────────── │  AI Seller Core      │
└─────────────────┘   (converted to Bitrix format) │  ┌────────────────┐  │
                                                  │  │ Bitrix Adapter │  │
                                                  │  │ - CRM          │  │
                                                  │  │ - IM           │  │
                                                  │  │ - Tasks        │  │
                                                  │  │ - Catalog      │  │
                                                  │  │ - Events       │  │
                                                  │  └───────┬────────┘  │
                                                  │          │           │
                                                  │  ┌───────┴────────┐  │
                                                  │  │ Dialog Engine  │  │
                                                  │  │ - Skills       │  │
                                                  │  │ - Rules        │  │
                                                  │  │ - Functions    │  │
                                                  │  └───────┬────────┘  │
                                                  │          │           │
                                                  │  ┌───────┴────────┐  │
                                                  │  │ LLM Interface │  │
                                                  │  │ Data Providers │  │
                                                  │  │ (Bitrix API)   │  │
                                                  │  └────────────────┘  │
                                                  └──────────────────────┘
```

**Режимы получения обновлений:**
- **Исходящие вебхуки** (настраиваются в UI: Разработчикам → Исходящий вебхук) — события CRM, IM, Tasks, Timeline
- **event.bind** (только для приложений OAuth) — регистрация обработчиков программно
- **Long Polling** — не рекомендуется для продакшена

---

### 4.3. Авторизация и токены

#### 4.3.1. Входящий вебхук (для внутренних интеграций)
```
URL: https://{domain}.bitrix24.ru/rest/{USER_ID}/{WEBHOOK_CODE}/{method}
```
- Создаётся в UI: Разработчикам → Вебхуки → Входящий вебхук
- Выдаются права (скоупы) при создании
- Токен не истекает, но привязан к пользователю

#### 4.3.2. OAuth 2.0 (для приложений)
```
1. Авторизация: https://{domain}.bitrix24.ru/oauth/authorize?client_id=...&response_type=code&redirect_uri=...
2. Обмен code на токен: POST https://oauth.bitrix24.tech/token
   grant_type=authorization_code&client_id=...&client_secret=...&code=...
3. Response: access_token, refresh_token, expires_in=3600, scope=...
4. Использование: POST /rest/method?auth=ACCESS_TOKEN
5. Обновление: grant_type=refresh_token&refresh_token=...
```

#### 4.3.3. REST 3.0 (новая версия)
```
URL: https://{domain}.bitrix24.ru/rest/api/{USER_ID}/{WEBHOOK_CODE}/{method}
# или для приложения:
POST /rest/api/{method}?auth=ACCESS_TOKEN
```
- Единый формат ответа, OpenAPI, Idempotency-Key, связанные данные
- SDK: `b24pysdk` (`prefer_version=3`), `b24gosdk`, `b24jssdk` (`$b24.actions.v3`)

---

### 4.4. Ключевые эндпоинты (REST 3.0 / v2)

#### CRM — Универсальные методы (рекомендуемые)
| Назначение | Метод | Путь (REST 3.0) | Описание |
|------------|-------|-----------------|----------|
| **Создать элемент** | `POST` | `/rest/api/crm.item.add` | `entityTypeId` (1=Лид, 2=Сделка, 3=Контакт, 4=Компания, 31=Счёт), `fields` |
| **Обновить элемент** | `POST` | `/rest/api/crm.item.update` | `entityTypeId`, `id`, `fields` (только изменяемые) |
| **Получить элемент** | `POST` | `/rest/api/crm.item.get` | `entityTypeId`, `id` |
| **Список элементов** | `POST` | `/rest/api/crm.item.list` | `entityTypeId`, `select`, `filter`, `order`, `start` (пагинация 50) |
| **Удалить элемент** | `POST` | `/rest/api/crm.item.delete` | `entityTypeId`, `id` |
| **Поля элемента** | `POST` | `/rest/api/crm.item.fields` | `entityTypeId` → конфигурация полей |

#### CRM — Товарные позиции (в сделках/лидах/предложениях/счётах)
| Назначение | Метод | Путь | Описание |
|------------|-------|------|----------|
| **Добавить позицию** | `POST` | `/rest/api/crm.item.productrow.add` | `ownerId`, `ownerType` (L/D/Q/SI/T...), `productId`/`productName`, `price`, `quantity`, `discount*`, `tax*` |
| **Сохранить набор** | `POST` | `/rest/api/crm.item.productrow.set` | Полная замена позиций (`productRows[]`) |
| **Список позиций** | `POST` | `/rest/api/crm.item.productrow.list` | `filter` (=ownerType, =ownerId), `order`, `start` |
| **Получить позицию** | `POST` | `/rest/api/crm.item.productrow.get` | `id` |
| **Доступные к оплате** | `POST` | `/rest/api/crm.item.productrow.getAvailableForPayment` | `ownerId`, `ownerType` |

#### CRM — Таймлайн (логи, комментарии, дела)
| Назначение | Метод | Путь | Описание |
|------------|-------|------|----------|
| **Лог-запись** | `POST` | `/rest/api/crm.timeline.logmessage.add` | `entityTypeId`, `entityId`, `title`, `text`, `iconCode` |
| **Комментарий** | `POST` | `/rest/api/crm.timeline.comment.add` | `entityTypeId`, `entityId`, `text`, `files[]` |
| **Дело (звонок/встреча/письмо)** | `POST` | `/rest/api/crm.activity.add` | `ownerTypeId`, `ownerId`, `typeId`, `subject`, `description`, `deadline` |

#### IM (Мессенджер) — Чат-боты и открытые линии
| Назначение | Метод | Путь | Описание |
|------------|-------|------|----------|
| **Отправить сообщение** | `POST` | `/rest/api/im.message.add` | `dialog_id`/`chat_id`, `message`, `attach[]` |
| **Получить диалоги** | `POST` | `/rest/api/im.dialog.list` | `limit`, `offset` |
| **История сообщений** | `POST` | `/rest/api/im.message.get` | `dialog_id`, `limit`, `offset` |
| **Прочитать** | `POST` | `/rest/api/im.dialog.read` | `dialog_id` |
| **Чат задачи** | `POST` | `/rest/api/tasks.task.chat.message.send` | `taskId`, `text` |

#### Tasks (Задачи)
| Назначение | Метод | Путь | Описание |
|------------|-------|------|----------|
| **Создать задачу** | `POST` | `/rest/api/tasks.task.add` | `fields`: `title`, `description`, `responsibleId`, `deadline`, `projectId` |
| **Обновить задачу** | `POST` | `/rest/api/tasks.task.update` | `taskId`, `fields` |
| **Получить задачу** | `POST` | `/rest/api/tasks.task.get` | `taskId` |
| **Список задач** | `POST` | `/rest/api/tasks.task.list` | `filter`, `order`, `select` |

#### Events (События) — Вебхуки
| Назначение | Метод | Путь | Описание |
|------------|-------|------|----------|
| **Подписаться (приложение)** | `POST` | `/rest/api/event.bind` | `event` (ONCRMLEADADD...), `handler` (URL), `auth_type`, `event_type` (online/offline) |
| **Подписаться (вебхук UI)** | — | Настройка в UI | Разработчикам → Исходящий вебхук → выбираем события |
| **Список событий** | `POST` | `/rest/api/events` | Доступные для приложения события |
| **Отписаться** | `POST` | `/rest/api/event.unbind` | `event`, `handler` |

**Ключевые события для ИИ-продавца:**
- `ONCRMLEADADD` — новый лид
- `ONCRMDEALADD` / `ONCRMDEALUPDATE` — сделка
- `ONCRMCONTACTADD` / `ONCRMCOMPANYADD` — контакт/компания
- `ONIMMESSAGEADD` — новое сообщение в чате
- `ONIMDIALOGMESSAGEADD` — сообщение в диалоге
- `ONTASKADD` / `ONTASKUPDATE` — задача
- `ONCRMACTIVITYADD` — дело в таймлайне

#### Catalog (Торговый каталог)
| Назначение | Метод | Путь | Описание |
|------------|-------|------|----------|
| **Товары** | `POST` | `/rest/api/catalog.product.*` | add/update/get/list, sections, properties, variations (SKU), images |
| **Цены** | `POST` | `/rest/api/catalog.price.*` | Типы цен, цены товаров |
| **Склады** | `POST` | `/rest/api/catalog.store.*` | Склады, остатки (`catalog.storeproduct.*`) |

#### OpenAPI (REST 3.0)
```
GET/POST /rest/api/rest.documentation.openapi
# или короткий синоним
/rest/api/documentation
```
Возвращает полную OpenAPI 3.0 схему текущего портала (методы, параметры, схемы).

---

### 4.5. Форматы сообщений (маппинг Unified ↔ Bitrix24)

#### 4.5.1. Входящее событие CRM (Webhook → Unified)

```python
# Bitrix Webhook: ONCRMLEADADD
{
    "event": "ONCRMLEADADD",
    "event_handler_id": "185",
    "data": {"FIELDS": {"ID": "7405"}},
    "ts": "1766047124",
    "auth": {"domain": "domain.bitrix24.ru", "member_id": "d897...", "application_token": "..."}
}

# → Наш адаптер делает crm.item.get(entityTypeId=1, id=7405)
# → UnifiedEvent (CRM entity event; не сообщение)
{
    "event_type": "crm_entity_change",
    "event_id": "generated-event-id",
    "provider_event_id": "ONCRMLEADADD:7405:1766047124",
    "channel_id": "bitrix24_crm:member_d897...",
    "channel_type": "bitrix24_crm",
    "received_at": "2025-12-18T12:00:00Z",
    "schema_version": "1",
    "correlation_id": "generated-correlation-id",
    "entity": {
        "entity_type": "lead",
        "entity_id": "7405",
        "change_type": "created",
        "contact_id": None
    },
    "metadata": {
        "event_type": "ONCRMLEADADD",
        "portal_domain": "domain.bitrix24.ru",
        "ts": 1766047124
    }
}
```

#### 4.5.2. Входящее сообщение IM (Webhook → Unified)

```python
# Bitrix Webhook: ONIMMESSAGEADD
{
    "event": "ONIMMESSAGEADD",
    "data": {
        "FIELDS": {
            "ID": "12345",
            "CHAT_ID": "chat_678",
            "AUTHOR_ID": "99",
            "MESSAGE": "Привет, интересует товар",
            "MESSAGE_TYPE": "text",
            "DATE_CREATE": "2025-01-22T10:00:00+03:00"
        }
    }
}

# → UnifiedEvent (message event; message-specific payload)
{
    "session_id": "bitrix24:im:chat_678",
    "channel_id": "bitrix24_im:member_d897...",
    "customer_id": "bitrix24:user:99",
    "message_id": "12345",
    "text": "Привет, интересует товар",
    "attachments": [],
    "callback_data": None,
    "metadata": {
        "chat_id": "chat_678",
        "author_id": 99,
        "message_type": "text",
        "created_at": "2025-01-22T10:00:00+03:00"
    }
}
```

#### 4.5.3. Исходящее сообщение (Unified → Bitrix)

```python
# Unified Response
{
    "text": "Здравствуйте! Товар в наличии. Цена 15 000 ₽. Хотите оформить заказ?",
    "cards": [
        {"title": "Товар", "description": "Характеристики...", "price": "15 000 ₽", "image_url": "https://..."}
    ],
    "keyboard": None
}

# → Bitrix IM: im.message.add
POST /rest/api/im.message.add
{
    "dialog_id": "chat_678",
    "message": "Здравствуйте! Товар в наличии. Цена 15 000 ₽. Хотите оформить заказ?",
    "attach": []  # файлы загружены через disk.folder.uploadfile
}

# → Bitrix CRM: создание сделки + товарные позиции
POST /rest/api/crm.item.add
{"entityTypeId": 2, "fields": {"title": "Сделка из чата", "stageId": "NEW", "contactIds": [123]}}
# Response: {"result": 456}

POST /rest/api/crm.item.productrow.set
{"ownerId": 456, "ownerType": "D", "productRows": [{"productId": 789, "price": 15000, "quantity": 1}]}

# → Таймлайн: лог-запись
POST /rest/api/crm.timeline.logmessage.add
{"fields": {"entityTypeId": 2, "entityId": 456, "title": "ИИ-продавец", "text": "Создана сделка из чата", "iconCode": "success"}}
```

---

### 4.6. Модель канала в БД (таблица `channels`)

```sql
CREATE TABLE channels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id),
    type VARCHAR(32) NOT NULL,  -- 'bitrix24_crm', 'bitrix24_im', 'bitrix24_tasks'
    name VARCHAR(255),          -- "Bitrix24: CRM + Chat"
    credentials JSONB NOT NULL, -- зашифрованные:
    -- вебхук: {"webhook_url": "https://domain.bitrix24.ru/rest/1/abc123/", "member_id": "d897..."}
    -- OAuth: {"access_token": "...", "refresh_token": "...", "expires_at": 1234567890, "client_id": "...", "client_secret": "...", "domain": "domain.bitrix24.ru"}
    inbound_mode VARCHAR(16) NOT NULL DEFAULT 'webhook',
    health_state VARCHAR(24) NOT NULL DEFAULT 'requires_setup',
    failure_domain VARCHAR(32),
    last_error TEXT,
    capabilities JSONB NOT NULL DEFAULT '{}',
    webhook_url VARCHAR(500),
    bitrix_member_id VARCHAR(64),  -- member_id портала
    bitrix_domain VARCHAR(255),    -- domain.bitrix24.ru
    rest_version VARCHAR(8) DEFAULT 'v2',  -- 'v2' | 'v3'
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_channels_project ON channels(project_id);
CREATE INDEX idx_channels_type_health ON channels(type, health_state);
```

**Пример `capabilities` для Bitrix24 CRM:**
```json
{
    "supports_crm_universal": true,
    "supports_crm_legacy": true,
    "supports_product_rows": true,
    "supports_timeline_logmessage": true,
    "supports_timeline_comment": true,
    "supports_timeline_activity": true,
    "supports_smart_processes": true,
    "entity_types": {
        "1": "lead", "2": "deal", "3": "contact", "4": "company", "31": "invoice"
    },
    "max_batch_size": 50,
    "rate_limit": "50 req/s per user (webhook), 2 req/s per app (OAuth)",
    "idempotency_key": true,
    "openapi_available": true
}
```

**Для Bitrix24 IM:**
```json
{
    "supports_dialogs": true,
    "supports_chats": true,
    "supports_open_lines": true,
    "supports_message_types": ["text", "file", "image", "video", "audio", "location", "contact", "sticker"],
    "supports_attachments": true,
    "max_message_length": 65535,
    "supports_webhook": true,
    "events": ["ONIMMESSAGEADD", "ONIMDIALOGMESSAGEADD", "ONIMCHATMESSAGEADD"]
}
```

---

### 4.7. Сессии и идентификация

| Bitrix сущность | Ядро (Unified) | Примечание |
|-----------------|----------------|------------|
| `member_id` (портал) | `project.external_ref` | Уникальный ID портала |
| `user_id` (сотрудник/клиент) | `customer_id` = `bitrix24:user:{user_id}` | Для IM-чатов |
| `contact_id` / `company_id` | `customer_id` = `bitrix24:contact:{id}` / `bitrix24:company:{id}` | Для CRM |
| `lead_id` / `deal_id` | `session_id` = `bitrix24:crm:lead:{id}` / `bitrix24:crm:deal:{id}` | CRM-сессия |
| `chat_id` / `dialog_id` | `session_id` = `bitrix24:im:chat:{id}` | IM-сессия |
| `task_id` | `session_id` = `bitrix24:task:{id}` | Задача |

**Customer Identities:**
```sql
-- Контакт из CRM
INSERT INTO customer_identities (customer_id, channel_type, external_id)
VALUES ('bitrix24:contact:123', 'bitrix24_crm', '123');

-- Пользователь чата
INSERT INTO customer_identities (customer_id, channel_type, external_id)
VALUES ('bitrix24:user:99', 'bitrix24_im', '99');
```

**Контекст CRM (F30):**
При работе в карточке сделки/лида — `entityTypeId` + `entityId` + все поля элемента передаются в навык как контекст.

---

### 4.8. Функции, доступные в Bitrix24 (реестр функций F6)

| Функция | Описание | Параметры | Требования (скоупы/права) |
|---------|----------|-----------|---------------------------|
| `crm_create_lead` | Создать лид | `fields` (title, name, phone, email, sourceId...) | `crm`, право добавления лидов |
| `crm_create_deal` | Создать сделку | `fields` (title, stageId, contactIds, companyId, opportunity...) | `crm`, право добавления сделок |
| `crm_update_deal` | Обновить сделку | `id`, `fields` (stageId, opportunity, custom fields...) | `crm`, право изменения сделок |
| `crm_get_deal` | Получить сделку | `id` | `crm`, право чтения сделок |
| `crm_list_deals` | Список сделок | `filter`, `select`, `order`, `start` | `crm` |
| `crm_add_product_rows` | Добавить товары в сделку | `ownerId`, `ownerType=D`, `productRows[]` | `crm`, право изменения сделок |
| `crm_set_product_rows` | Заменить товары в сделке | `ownerId`, `ownerType=D`, `productRows[]` | `crm` |
| `crm_get_product_rows` | Товары сделки | `filter` (=ownerType=D, =ownerId) | `crm` |
| `crm_add_timeline_log` | Запись в таймлайн | `entityTypeId`, `entityId`, `title`, `text`, `iconCode` | `crm`, право изменения элемента |
| `crm_add_timeline_comment` | Комментарий в таймлайн | `entityTypeId`, `entityId`, `text`, `files[]` | `crm` |
| `im_send_message` | Отправить сообщение в чат | `dialog_id`/`chat_id`, `message`, `attach[]` | `im`, `imopenlines` (для открытых линий) |
| `im_get_dialogs` | Список диалогов | `limit`, `offset` | `im` |
| `im_get_messages` | История чата | `dialog_id`, `limit`, `offset` | `im` |
| `task_create` | Создать задачу | `fields` (title, responsibleId, deadline...) | `task` |
| `task_update` | Обновить задачу | `taskId`, `fields` | `task` |
| `catalog_search_products` | Поиск товаров | `filter`, `select` | `catalog` |
| `catalog_get_product` | Товар по ID | `id` | `catalog` |
| `event_bind` | Подписаться на событие | `event`, `handler`, `auth_type` | OAuth app, соответствующий скоуп |

---

### 4.9. Настройка вебхуков (Deployment)

#### 4.9.1. Исходящий вебхук (UI)
1. В Битрикс24: **Разработчикам → Другое → Исходящий вебхук**
2. URL: `https://your-core-domain.com/api/v1/webhooks/bitrix24/{channel_id}`
3. События: `ONCRMLEADADD`, `ONCRMDEALUPDATE`, `ONIMMESSAGEADD`, `ONTASKADD`, `ONCRMACTIVITYADD`
4. Сохранить → сгенерируется `application_token` (проверяем в заголовке/теле)

#### 4.9.2. Обработчик в ядре
```python
@router.post("/webhooks/bitrix24/{channel_id}")
async def bitrix24_webhook(channel_id: str, request: Request):
    # 1. Проверить предусмотренный для данного способа авторизации секрет/токен.
    try:
        payload = await request.json()
    except JSONDecodeError:
        raise HTTPException(400, "Invalid JSON payload")
    app_token = payload.get("auth", {}).get("application_token")
    if not bitrix_authenticator.verify(channel, app_token, payload):
        raise HTTPException(401, "Invalid Bitrix24 webhook credentials")
    
    # 2. CRM/IM/Tasks — разные event types; только IM-сообщение может
    # инициировать диалог, CRM/task events направляются отдельным handlers.
    events = bitrix_adapter.parse_inbound(payload, dict(request.headers))
    await inbox_service.persist_events_and_jobs(channel, events)
    return bitrix_adapter.webhook_ack()

async def route_bitrix_event(channel_id: str, event: str, data: dict, auth: dict):
    if event.startswith("ONCRM"):
        await process_crm_event(channel_id, event, data)
    elif event.startswith("ONIM"):
        await process_im_event(channel_id, event, data)
    elif event.startswith("ONTASK"):
        await process_task_event(channel_id, event, data)
```

#### 4.9.3. OAuth приложение (для маркетплейса/мульти-портал)
- Регистрация в Битрикс24 Маркетплейс или локально
- `client_id`, `client_secret` хранятся в `channels.credentials` (зашифрованные)
- Авто-обновление `access_token` через `refresh_token` при 401

---

### 4.10. Обработка ошибок и ретраи

| HTTP код / Error | Действие |
|------------------|----------|
| `200` + `result` | Успех |
| `BITRIX_REST_V3_EXCEPTION_INSUFFICIENTSCOPEEXCEPTION` (403) | Нет скоупа — `health_state=failed`, `failure_domain=permissions`, показать требуемый scope |
| `BITRIX_REST_V3_EXCEPTION_ACCESSDENIEDEXCEPTION` (403) | Нет прав пользователя — логировать, не ретраить |
| `BITRIX_REST_V3_EXCEPTION_VALIDATION_REQUESTVALIDATIONEXCEPTION` (400) | Ошибка валидации — логировать, не ретраить |
| `ERROR_METHOD_NOT_FOUND` (404) | Метод не существует в REST 3.0 — фоллбек на v2 |
| `QUERY_LIMIT_EXCEEDED` / 429 | Rate limit — бэкофф (webhook: 50 req/s, OAuth app: 2 req/s) |
| `EXPIRED_TOKEN` / 401 | Токен истёк — обновить через refresh_token, ретраить 1 раз |
| `500` / `503` | Ошибка Битрикс — экспоненциальный бэкофф |

**Rate Limits:**
- **Вебхук:** ~50 запросов/сек на пользователя (заголовки не возвращаются, эмпирически)
- **OAuth приложение:** 2 запроса/сек на приложение (строго)
- **Batch API** (v2): до 50 методов за вызов — для массовых операций

---

### 4.11. Idempotency (REST 3.0)

Для критических операций (создание сделки, лида, отправка сообщения) используем заголовок:
```python
headers = {
    "Idempotency-Key": f"{channel_id}:{operation}:{unique_key}"  # UUID
}
```
- Первый вызов выполняется, ответ кэшируется на 24 часа
- Повтор с тем же ключом и телом → возвращается сохранённый ответ + заголовок `Idempotent-Replayed: true`

---

### 4.12. Тестирование интеграции

**Локальная разработка:**
```bash
ngrok http 8000
# → https://abc123.ngrok-free.app
WEBHOOK_BASE_URL=https://abc123.ngrok-free.app
```

**Тестовый портал:**
- Регистрация тестового портала на `bitrix24.ru` (бесплатный тариф)
- Создание входящего вебхука с полными правами
- Настройка исходящего вебхука на ngrok URL
- Тестовые данные: лиды, сделки, контакты, чаты, задачи

**Автотесты (F28):**
- Моки вебхуков всех типов событий (CRM, IM, Tasks, Timeline)
- Проверка универсальных методов CRM (entityTypeId 1,2,3,4,31)
- Проверка товарных позиций (add/set/list/get)
- Проверка OAuth token refresh flow
- Проверка Idempotency-Key
- Проверка пагинации (start=0,50,100...)

---

### 4.13. Чек-лист готовности к продакшену

- [ ] Адаптеры CRM / IM / Tasks реализованы и покрыты тестами
- [ ] OAuth 2.0 flow (Authorization Code + Refresh Token) работает
- [ ] Входящий вебхук для внутренних интеграций
- [ ] Исходящий вебхук зарегистрирован в UI, события приходят
- [ ] Токены/секреты хранятся зашифрованными
- [ ] Универсальные методы CRM (`crm.item.*`) используются вместо deprecated (`crm.lead.add` и т.д.)
- [ ] Товарные позиции: `set` для полной замены, `add` для добавления
- [ ] Таймлайн: лог-записи для системных сообщений, комментарии для диалога
- [ ] **Синхронизация каталога** (F4): `catalog.product.*` → наш каталог
- [ ] **Контекст карточки (F30)**: при входе в сделку/лид — подтягиваем все поля
- [ ] Rate limiting: токен-бакет на канал
- [ ] Idempotency-Key для критических операций
- [ ] Логирование (без ПДн, F17)
- [ ] Метрики: latency, error rate, webhook events/sec, token refresh rate
- [ ] Документация для админов: как подключить портал, выдать права, настроить вебхуки

---

### 4.14. Известные ограничения Bitrix24 REST API

1. **Две версии API одновременно** — v1/v2 (старая) и REST 3.0 (новая). Не все методы переведены на 3.0.
2. **Пагинация:** всегда 50 записей на страницу (`start` параметр). Нет `limit`.
3. **Batch API** (v2): `batch` метод для группировки до 50 вызовов. В REST 3.0 — нет batch, вызывать по отдельности.
4. **Rate limits:** вебхук — мягкие (~50 rps), OAuth приложение — строгие (2 rps).
5. **Права доступа:** метод вернёт только то, что видит пользователь-авторизованный. Админ видит всё, менеджер — только свои.
6. **Смарт-процессы:** только через универсальные методы (`entityTypeId` из `crm.type.list`).
7. **Пользовательские поля (UF_*):** формат имен `useOriginalUfNames: Y/N`. В фильтрах boolean — `1/0`, не `Y/N`.
8. **Файлы/Диск:** загрузка через `disk.folder.uploadfile` → `file_id` → прикрепление к сообщению/CRM.
9. **Открытые линии (IM коннекторы):** отдельные скоупы `imopenlines`, сообщения приходят как `ONIMMESSAGEADD` с `CHAT_TYPE=open_line`.
10. **Idempotency-Key** только в REST 3.0 (`/rest/api/`).

---

### 4.15. Полезные репозитории и инструменты

| Репозиторий | Описание |
|-------------|----------|
| **[bitrix-tools/b24-rest-docs](https://github.com/bitrix-tools/b24-rest-docs)** ⭐ | Официальный репозиторий документации (Markdown, Diplodoc). Исходники всех страниц apidocs.bitrix24.ru. |
| **[bitrix-tools/b24pysdk](https://github.com/bitrix-tools/b24pysdk)** | Официальный Python SDK. Поддержка REST 3.0 (`prefer_version=3`). |
| **[bitrix-tools/b24gosdk](https://github.com/bitrix-tools/b24gosdk)** | Официальный Go SDK. Базовый адрес с `/rest/api/`. |
| **[bitrix-tools/b24jssdk](https://github.com/bitrix-tools/b24jssdk)** | Официальный JS/TS SDK. `$b24.actions.v3` для REST 3.0. |
| **[bitrix-tools/b24-ai-starter](https://github.com/bitrix-tools/b24-ai-starter)** | Шаблон для ИИ-агентов с MCP-сервером. |

**MCP-сервер для ИИ-агентов:** `https://apidocs.bitrix24.ru/ai-tools/mcp.html` — позволяет ИИ-агенту обращаться к официальной документации при разработке.

---

### 4.16. Сценарий: «Отчёты от Авито/Озон/MAX → CRM Битрикс24 + Клиентская БД»

**Поток данных:**
```
1. Авито/Озон/MAX Adapter получает диалог/заказ
   ↓
2. Dialog Engine обрабатывает, извлекает сущности (товар, цена, контакт, комментарий)
   ↓
3. Function Registry вызывает:
   - crm_create_lead/deal (если новый клиент)
   - crm_get_contact/company (поиск по телефону/email/ИНН)
   - crm_update_deal (стадия, сумма, товарные позиции)
   - crm_set_product_rows (товары из каталога Авито/Озон → привязка к каталогу Битрикс)
   - crm_add_timeline_log (история диалога, ссылки на исходные сообщения)
   - im_send_message (уведомление менеджеру в чат Битрикс24)
   ↓
4. Клиентская БД (наша):
   - customers: внешние ID (avito:123, ozon:456, max:789) → bitrix24:contact:789
   - orders: external_id + bitrix_deal_id + source_channel
   - messages: синхронизация истории для контекста
   - analytics: конверсия, средний чек, время ответа
```

**Маппинг полей (пример):**
| Источник | Поле | → Bitrix24 CRM |
|----------|------|----------------|
| Авито `chat_id` | `dialog_variables.avito_chat_id` | `UF_CRM_AVITO_CHAT_ID` (UF поле сделки) |
| Озон `posting_number` | `dialog_variables.posting_number` | `UF_CRM_OZON_POSTING` |
| MAX `chat_id` | `dialog_variables.max_chat_id` | `UF_CRM_MAX_CHAT_ID` |
| Телефон клиента | `customer_variables.phone` | `CONTACT.PHONE` / `LEAD.PHONE` |
| Email | `customer_variables.email` | `CONTACT.EMAIL` / `LEAD.EMAIL` |
| Товары (SKU, цена, кол-во) | `cart_items` | `crm.item.productrow.set` (productId из каталога) |
| История сообщений | `chat_messages` | `crm.timeline.logmessage.add` (пачкой за диалог) |

---

### 4.17. Ссылки для команды

- [Официальная документация](https://apidocs.bitrix24.ru/)
- [GitHub Docs](https://github.com/bitrix-tools/b24-rest-docs) — можно клонировать для офлайн-поиска
- [Python SDK](https://github.com/bitrix-tools/b24pysdk) — `pip install b24pysdk`
- [OpenAPI метод](https://apidocs.bitrix24.ru/api-reference/rest-v3.html#openapi) — `rest.documentation.openapi`
- [Скоупы и права](https://apidocs.bitrix24.ru/api-reference/scopes/permissions.html)
- [Лимиты REST API](https://apidocs.bitrix24.ru/limits.html)
- [Коды ошибок](https://apidocs.bitrix24.ru/error-codes.html)

---

### Приложение А: Примеры payloads (для тестов)

#### A.1. Webhook: ONCRMLEADADD
```json
{
  "event": "ONCRMLEADADD",
  "event_handler_id": "185",
  "data": {"FIELDS": {"ID": "7405"}},
  "ts": "1766047124",
  "auth": {
    "domain": "domain.bitrix24.ru",
    "member_id": "d897063e1ce7c5eb9f04b9751eef5915",
    "application_token": "jvh9y1ulvt2m6k5or90v9mg8nn32ozas"
  }
}
```

#### A.2. Webhook: ONIMMESSAGEADD (открытая линия)
```json
{
  "event": "ONIMMESSAGEADD",
  "event_handler_id": "975",
  "data": {
    "FIELDS": {
      "ID": "12345",
      "CHAT_ID": "chat_678",
      "AUTHOR_ID": "99",
      "MESSAGE": "Здравствуйте, хочу купить",
      "MESSAGE_TYPE": "text",
      "DATE_CREATE": "2025-01-22T10:00:00+03:00",
      "CHAT_TYPE": "open_line"
    }
  },
  "auth": {"domain": "domain.bitrix24.ru", "member_id": "d897...", "application_token": "..."}
}
```

#### A.3. Создание сделки с товарами (REST 3.0)
```bash
# 1. Создать сделку
curl -X POST "https://domain.bitrix24.ru/rest/api/1/abc123/crm.item.add" \
  -H "Content-Type: application/json" \
  -d '{"entityTypeId":2,"fields":{"title":"Заказ из Авито","stageId":"NEW","contactIds":[123],"opportunity":15000,"currencyId":"RUB","sourceId":"WEB","ufCrmAvitoChatId":"chat_xyz"}}'

# Response: {"result": 456, "time": {...}}

# 2. Добавить товарные позиции
curl -X POST "https://domain.bitrix24.ru/rest/api/1/abc123/crm.item.productrow.set" \
  -H "Content-Type: application/json" \
  -d '{"ownerId":456,"ownerType":"D","productRows":[{"productId":789,"price":15000,"quantity":1,"taxRate":20,"taxIncluded":"Y","measureCode":796}]}'

# 3. Лог в таймлайн
curl -X POST "https://domain.bitrix24.ru/rest/api/1/abc123/crm.timeline.logmessage.add" \
  -H "Content-Type: application/json" \
  -d '{"fields":{"entityTypeId":2,"entityId":456,"title":"ИИ-продавец (Авито)","text":"Создана сделка из чата Авито. Диалог: chat_xyz","iconCode":"success"}}'
```

#### A.4. Отправка сообщения менеджеру в чат задачи
```bash
curl -X POST "https://domain.bitrix24.ru/rest/api/1/abc123/tasks.task.chat.message.send" \
  -H "Content-Type: application/json" \
  -d '{"fields":{"taskId":789,"text":"Новый лид из Авито! Сделка #456. Клиент: Иван (+7 999 123 45 67). Товар: iPhone 15 Pro 256GB."}}'
```

---

### Приложение Б: Маппинг entityTypeId

| entityTypeId | Объект | Код ownerType (товарные позиции) | Описание |
|--------------|--------|----------------------------------|----------|
| `1` | Лид | `L` | Входящая заявка |
| `2` | Сделка | `D` | Продажа |
| `3` | Контакт | — | Физическое лицо |
| `4` | Компания | — | Юридическое лицо |
| `5` | Предложение | `Q` | Коммерческое предложение |
| `31` | Счёт (новый) | `SI` | Счёт на оплату |
| `128+` | Смарт-процесс | `T` + hex(entityTypeId) | Пользовательские типы |

---

---

# 5. Generic Channel Integration Framework (F31) — справочный шаблон расширения

> **Граница MVP-1:** это материал о расширяемости, а не самостоятельное ТЗ и не требование реализовать универсальную интеграционную платформу. В MVP-1 реализуется только необходимый MAX-адаптер и общий внутренний контракт, требуемый ТЗ. Ozon, Avito, CRM/ERP, телефония и примеры других каналов — последующие этапы.
>
> F31 задаёт принцип расширения, а не публичный универсальный REST API и не утверждает подключение каждого перечисленного provider.
>
> Provider-specific сведения в примерах требуют проверки по актуальным официальным источникам.

---

## 5.1. Архитектурные принципы

| Принцип | Описание |
|---------|----------|
| **Protocol over Implementation** | Базовый `ChannelAdapter` — это `Protocol`/`ABC` с обязательными методами. Конкретные адаптеры только реализуют интерфейс. |
| **Capability-Driven** | Канал объявляет `ChannelCapabilities` и ограничения активной сессии. Runtime передаёт их в LLM до генерации, затем валидирует и конвертирует ответ детерминированно. |
| **Event-First Contract** | Единый `UnifiedEvent` описывает любое входящее событие; сообщение/callback — отдельные типизированные payloads, а не обязательная форма для каждого события. |
| **Credential Abstraction** | `CredentialProvider` + `TokenManager` — единая работа с секретами (статические, OAuth 2.0, API-ключи, вебхук-секреты). |
| **Session Strategy** | `SessionResolver` на канал — как из входящего сделать `session_id` + `customer_id`. |
| **Context Extraction** | `ContextExtractor` — извлечение контекста товара/заказа/страницы (F30) из входящего события. |
| **Durable Webhook Ingestion** | Webhook: верификация → нормализация → атомарная запись Inbox + durable job → ACK по контракту provider. Обработка выполняется worker, не in-memory background task. |
| **Rate Limiting** | Provider-aware limiter с конфигом; Redis может быть ускорением, но не обязательным компонентом MVP-1 и не источником истины для durable jobs/Inbox/Outbox. |
| **Media Pipeline** | `MediaUploader` — стратегия загрузки на канал (multipart, 2-step, 3-step, предзагрузка). |
| **Idempotency** | В MVP-1 долговременная дедупликация Inbox и состояние Outbox опираются на PostgreSQL. Redis TTL может быть только дополнительным ускорением и не является обязательным или единственным механизмом защиты. |
| **Health Classification** | `healthy/degraded/failed/requires_setup/disabled` с доменом причины (`credentials`, `permissions`, `subscription`, `provider_outage`, `rate_limit`, `configuration`, `unknown`). |
| **Observability** | Структурированное логирование, метрики, трейсинг на уровне фреймворка. |

---

## 5.2. Компоненты фреймворка

Ниже — иллюстративная структура модульного монолита, а не требование создавать микросервисы. В MVP-1 реализуется только нужный MAX-адаптер и части общих контрактов, указанные в BACKEND_MVP_TZ.md. Путь webhook — внешний ingress провайдера; это не публичный клиентский/покупательский API.

```
ai_seller_core/
├── adapters/
│   ├── base.py                    # ChannelAdapter (Protocol), ChannelCapabilities, Unified* модели
│   ├── registry.py                # AdapterRegistry — регистрация/поиск по channel_type
│   ├── generic_webhook.py         # Проверка + durable Inbox/job + provider-specific ACK
│   ├── event_router.py            # Маршрутизация событий по доменным обработчикам
│   ├── session_strategy.py        # SessionResolver — стратегии сессий
│   ├── context_extractor.py       # ContextExtractor — F30 контекст
│   ├── credential.py              # CredentialProvider, TokenManager, CredentialSchema
│   ├── rate_limiter.py            # RateLimiter — токен-бакет на канал
│   ├── media.py                   # MediaUploader — стратегии загрузки медиа
│   ├── idempotency.py             # Durable Inbox + outgoing idempotency keys
│   ├── testing/
│   │   ├── harness.py             # ChannelTestHarness — базовые тесты для адаптеров
│   │   └── fixtures/              # Фикстуры вебхуков на канал
│   └── max/                       # Пример: MAX адаптер
│       ├── client.py
│       ├── models.py
│       ├── webhook.py
│       └── converter.py
├── channels/
│   ├── models.py                  # Channel DB модель + Capabilities JSON
│   ├── service.py                 # ChannelService — CRUD, вебхук регистрация
│   └── capabilities.py            # ChannelCapabilities датакласс
├── core/
│   ├── schemas/
│   │   └── unified.py             # UnifiedEvent, message payload, UnifiedResponse и вложения
│   └── webhook.py                 # WebhookVerifier, WebhookRouter
└── api/v1/webhooks/
    └── generic.py                 # POST /webhooks/{channel_type}/{channel_id}
```

---

## 5.3. Базовые контракты (Pydantic модели)

### 5.3.1. UnifiedEvent (входящее событие)

```python
# core/schemas/unified.py
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime
from enum import Enum

class MessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    FILE = "file"
    LOCATION = "location"
    CONTACT = "contact"
    STICKER = "sticker"
    ITEM = "item"           # товар/объявление (Avito, Bitrix)
    LINK = "link"           # ссылка с превью
    TEMPLATE = "template"   # шаблон (Ozon)

class UnifiedAttachment(BaseModel):
    type: MessageType
    url: Optional[str] = None           # CDN URL (для входящих)
    token: Optional[str] = None         # токен для повторной отправки
    content: Optional[bytes] = None     # бинарник (для исходящих загрузки)
    filename: Optional[str] = None
    size: Optional[int] = None
    mime_type: Optional[str] = None
    # Метаданные по типам
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[int] = None
    # Для item/link
    title: Optional[str] = None
    description: Optional[str] = None
    price: Optional[str] = None
    item_url: Optional[str] = None
    # Для location
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    # Для contact
    contact_data: Optional[Dict[str, Any]] = None

class UnifiedKeyboard(BaseModel):
    buttons: List[List["UnifiedButton"]] = []
    inline: bool = True
    one_time: bool = False
    resize_keyboard: bool = True

class UnifiedButton(BaseModel):
    type: Literal["callback", "url", "text", "contact_request", "location_request", "web_app"]
    text: str
    payload: Optional[str] = None       # для callback
    url: Optional[str] = None           # для url
    web_app_url: Optional[str] = None   # для web_app
    web_app_data: Optional[str] = None

class MessageMetadata(BaseModel):
    # Канал-специфичные поля
    raw_payload: Dict[str, Any] = {}
    channel_specific: Dict[str, Any] = {}
    # Стандартные
    created_at: Optional[datetime] = None
    author_id: Optional[str] = None
    author_name: Optional[str] = None
    author_type: Optional[str] = None   # customer, bot, operator, system
    chat_type: Optional[str] = None     # dialog, group, channel, open_line
    message_subtype: Optional[str] = None  # forward, reply, edit
    reply_to_message_id: Optional[str] = None
    # Контекст (F30)
    item_context: Optional[Dict[str, Any]] = None  # товар/заказ/страница
    utm: Optional[Dict[str, str]] = None
    start_payload: Optional[str] = None

class InboundEventType(str, Enum):
    MESSAGE = "message"
    CALLBACK = "callback"
    CALL_EVENT = "call_event"
    MESSAGE_DELETED = "message_deleted"
    SYSTEM_NOTIFICATION = "system_notification"
    MESSAGE_STATUS = "message_status"
    CONVERSATION_LIFECYCLE = "conversation_lifecycle"
    CHANNEL_LIFECYCLE = "channel_lifecycle"
    CRM_ENTITY_CHANGE = "crm_entity_change"
    TASK_CHANGE = "task_change"
    PROVIDER_NOTIFICATION = "provider_notification"
    UNSUPPORTED = "unsupported"

class MessagePayload(BaseModel):
    # Эти поля требуются только для события, содержащего сообщение/обращение.
    session_id: Optional[str] = None
    customer_id: Optional[str] = None
    message_id: Optional[str] = None
    author_type: Optional[str] = None  # customer, operator, bot, system
    type: MessageType = MessageType.TEXT
    text: Optional[str] = None
    attachments: List[UnifiedAttachment] = []
    keyboard: Optional[UnifiedKeyboard] = None
    callback_data: Optional[str] = None
    metadata: MessageMetadata = Field(default_factory=MessageMetadata)

class UnifiedEvent(BaseModel):
    # event_id — внутренний ID; provider_event_id — ключ дедупликации, если он предоставлен API.
    event_id: str
    provider_event_id: Optional[str] = None
    channel_id: str
    channel_type: str
    event_type: InboundEventType
    occurred_at: Optional[datetime] = None
    received_at: datetime
    schema_version: str
    correlation_id: str
    message: Optional[MessagePayload] = None
    entity: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

class IngestResult(BaseModel):
    accepted: bool
    duplicate: bool
    job_ids: List[str] = []
```

`message` обязателен только для `MESSAGE`/`CALLBACK` и тех event types, чья схема действительно содержит сообщение. CRM-изменение, звонок Avito, закрытие чата Ozon, изменение прав/доступности канала MAX и прочее немесседжное событие представляются своим `event_type` и типизированными полями `entity/metadata`; пустой текст не используется как универсальный контейнер.

| Семейство события | Примеры | Маршрут обработки по умолчанию |
|---|---|---|
| `message` | Сообщение покупателя, входящее медиа/объявление | Inbox → customer/session resolution → Dialog Runtime |
| `callback` | Нажатие кнопки/действие пользователя | Inbox → callback handler или Dialog Runtime |
| `call_event` | Пропущенный/состоявшийся звонок, если API передаёт событие | Inbox → call/lead handler; не создавать текстовое сообщение |
| `message_deleted` | Сообщение удалено/отозвано | Inbox → синхронизировать состояние истории; не запускать LLM |
| `system_notification` | Системное событие поставщика | Integration handler → диагностика/health; не запускать LLM |
| `message_status` | Прочтение/доставка сообщения, если API предоставляет событие | Inbox → обновление статуса; не запускать LLM |
| `conversation_lifecycle` | Чат закрыт, покупатель остановил/заблокировал бота | Inbox → обновить состояние сессии; не отправлять обычный ответ |
| `channel_lifecycle` | Бот добавлен/удалён, изменены права, подписка webhook | Channel Registry / health monitor; не создавать сессию покупателя |
| `crm_entity_change` | Добавлена/изменена сделка, лид или контакт | CRM event handler; обновить локальную связь/статус по настроенной политике |
| `task_change` | Создана/обновлена задача в CRM | Task handler/уведомление по настроенному сценарию |
| `provider_notification` | Системное предупреждение или неизвестный поддерживаемый notice | Integration event handler; сохранить/показать администратору |

#### Дедупликация provider events

1. Если поставщик прислал стабильный ID события, использовать ключ `(channel_id, provider_event_id)` с уникальным ограничением в долговременной таблице `inbox`.
2. Если отдельного event ID нет, использовать документированный стабильный ID сущности/сообщения вместе с event type и идентификатором подключения. Хеш сырого запроса допустим только как явно описанный fallback с ограниченным сроком хранения; он не заменяет provider event ID там, где он есть.
3. Inbox insert и создание фоновой задачи выполняются в одной транзакции. При конфликте уникальности endpoint возвращает требуемый provider ACK, не создавая новую работу.
4. TTL-кэш в Redis допустим только как ускорение. Потеря кэша не должна снова запускать обработанный event.

### 5.3.2. UnifiedResponse (исходящее)

```python
class UnifiedCard(BaseModel):
    title: str
    description: Optional[str] = None
    image_url: Optional[str] = None
    price: Optional[str] = None
    currency: Optional[str] = None
    item_url: Optional[str] = None
    buttons: List[UnifiedButton] = []

class UnifiedResponse(BaseModel):
    text: Optional[str] = None
    cards: List[UnifiedCard] = []
    keyboard: Optional[UnifiedKeyboard] = None
    parse_mode: Literal["plain", "markdown", "html"] = "plain"
    silent: bool = False
    # Медиа для отправки
    media: List[UnifiedAttachment] = []
    # Отложенная отправка
    schedule_at: Optional[datetime] = None
    # Ответ на callback (для кнопок)
    callback_answer: Optional[str] = None
    callback_show_alert: bool = False
```

### 5.3.3. ChannelCapabilities

```python
class ChannelCapabilities(BaseModel):
    # Текст
    max_message_length: int = 4096
    supports_formatting: bool = False
    supported_formats: List[Literal["markdown", "html", "plain"]] = ["plain"]
    
    # Медиа
    supports_outgoing_media: bool = False
    supported_media_types: List[MessageType] = []
    max_file_size_mb: int = 20
    max_image_megapixels: int = 50
    
    # Карточки/товары
    supports_cards: bool = False
    supports_item_preview: bool = False
    
    # Клавиатуры
    supports_keyboards: bool = False
    supports_inline_keyboards: bool = False
    supports_callback_buttons: bool = False
    supports_url_buttons: bool = False
    supports_contact_request: bool = False
    supports_location_request: bool = False
    supports_web_app_buttons: bool = False
    max_buttons_per_row: int = 5
    max_keyboard_rows: int = 10
    
    # Callback
    callback_payload_max_length: int = 1024
    
    # Вебхуки
    supports_webhook: bool = True
    supports_polling: bool = False
    webhook_timeout_seconds: int = 30
    webhook_requires_443: bool = True
    webhook_signature_header: Optional[str] = None
    
    # Сессии
    session_per_user_in_group: bool = True
    supports_typing_indicator: bool = False
    supports_message_edit: bool = False
    supports_message_delete: bool = False
    delete_window_minutes: Optional[int] = None
    
    # Rate limits
    rate_limit_rps: Optional[float] = None
    rate_limit_burst: Optional[int] = None
    rate_limit_headers: List[str] = []
    
    # Авторизация
    auth_type: Literal["static_token", "api_key_pair", "oauth2_client_credentials", "oauth2_auth_code", "webhook_secret"]
    required_scopes: List[str] = []
    
    # Подписка/тарифы
    requires_subscription: List[str] = []
    subscription_access_verified: Optional[bool] = None
    
    # Контекст (F30)
    has_native_item_context: bool = False
    item_context_fields: List[str] = []
    
    # Специфичные фичи
    features: Dict[str, Any] = {}
```

В `ChannelCapabilities` записываются возможности API/канала. Перед каждым ответом runtime дополняет их ограничениями конкретной сессии, например оставшимся SLA-окном:

```python
class ChannelResponseConstraints(BaseModel):
    allowed_response_types: List[Literal["text", "card", "keyboard", "media"]]
    max_text_length: int
    supported_formats: List[Literal["plain", "markdown", "html"]]
    can_send_media: bool
    can_send_cards: bool
    can_send_buttons: bool
    reply_deadline: Optional[datetime] = None
    seconds_remaining: Optional[int] = None
```

`ChannelResponseConstraints` передаётся LLM-интерфейсу **до генерации** в системном контексте и, если поддерживается выбранным provider, в schema/structured output. В MVP-1 этот интерфейс обращается только к одному выбранному provider и не выполняет маршрутизацию или переключение на другой provider; ограниченная коррекция ответа, если она нужна, использует тот же provider. Модель инструктируется не генерировать недоступные элементы и укладываться в лимит длины. Это оптимизация качества и расходов, но не security boundary: после ответа модели Response Validator обязательно проверяет ограничения. При нарушении runtime может ограниченно запросить коррекцию у того же provider или сформировать безопасный ответ кодом; недопустимое содержимое не должно молча исчезать при конвертации.

### 5.3.4. Состояние подключения канала

```python
class ChannelHealthState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"
    REQUIRES_SETUP = "requires_setup"
    DISABLED = "disabled"

class FailureDomain(str, Enum):
    CREDENTIALS = "credentials"
    PERMISSIONS = "permissions"
    SUBSCRIPTION = "subscription"
    PROVIDER_OUTAGE = "provider_outage"
    RATE_LIMIT = "rate_limit"
    CONFIGURATION = "configuration"
    UNKNOWN = "unknown"
```

Статус хранится отдельно от причины. `degraded` означает временную/частичную проблему provider: очередь и принятые данные сохраняются, автоматические повторы ограничены, от клиента не требуют менять корректные credentials. `failed`/`requires_setup` используются для устранимой ошибки настройки, credentials, permissions или подтверждённого entitlement. Если provider не позволяет различить причины, фиксируется `unknown`, а интерфейс не должен утверждать, что виноват тариф или ключ.

---

## 5.4. ChannelAdapter Protocol

```python
# adapters/base.py
from typing import Protocol, Optional, Dict, Any, List
from core.schemas.unified import (
    UnifiedEvent, UnifiedResponse, UnifiedAttachment,
    UnifiedKeyboard, MessagePayload, ChannelCapabilities,
    ChannelResponseConstraints, MessageMetadata
)
from ai_seller_core.channels.models import Channel

class CredentialProvider(Protocol):
    async def get_credentials(self, channel: Channel) -> Dict[str, Any]: ...
    async def update_credentials(self, channel: Channel, creds: Dict[str, Any]) -> None: ...
    async def refresh_if_needed(self, channel: Channel) -> bool: ...

class TokenManager(Protocol):
    async def get_valid_token(self, channel: Channel) -> str: ...
    async def refresh_token(self, channel: Channel) -> str: ...

class SessionResolver(Protocol):
    # Вызывается Event Router только после выбора message/callback handler.
    def resolve_session_id(self, inbound: MessagePayload) -> Optional[str]: ...
    def resolve_customer_id(self, inbound: MessagePayload) -> Optional[str]: ...
    def extract_context(self, inbound: MessagePayload) -> Dict[str, Any]: ...

class MediaUploader(Protocol):
    async def upload(self, channel: Channel, file: bytes, media_type: str, filename: str) -> str: ...
    async def get_media_url(self, channel: Channel, token: str) -> str: ...

class RateLimiter(Protocol):
    async def acquire(self, channel_type: str, tokens: int = 1) -> None: ...
    def get_limit_info(self, channel_type: str) -> Dict[str, Any]: ...

class IdempotencyManager(Protocol):
    async def ingest(self, channel_id: str, event: UnifiedEvent) -> IngestResult: ...

class ChannelAdapter(Protocol):
    # Идентификация
    channel_type: str
    capabilities: ChannelCapabilities
    
    # Зависимости (инжектятся через registry)
    credential_provider: CredentialProvider
    token_manager: TokenManager
    session_resolver: SessionResolver
    media_uploader: MediaUploader
    rate_limiter: RateLimiter
    idempotency: IdempotencyManager
    
    # Входящие события
    async def parse_inbound(self, raw_payload: Dict[str, Any], headers: Dict[str, str]) -> List[UnifiedEvent]: ...
    async def verify_webhook(self, request: Request, channel: Channel) -> bool: ...
    
    # Исходящие сообщения
    async def send(self, channel: Channel, session_id: str, response: UnifiedResponse, idempotency_key: str) -> Dict[str, Any]: ...
    async def send_typing(self, channel: Channel, session_id: str, action: str) -> None: ...
    async def edit_message(self, channel: Channel, message_id: str, response: UnifiedResponse) -> bool: ...
    async def delete_message(self, channel: Channel, message_id: str) -> bool: ...
    async def answer_callback(self, channel: Channel, callback_id: str, text: str, show_alert: bool) -> bool: ...
    
    # Вебхуки
    async def register_webhook(self, channel: Channel, base_url: str) -> Dict[str, Any]: ...
    async def unregister_webhook(self, channel: Channel) -> bool: ...
    async def get_webhook_info(self, channel: Channel) -> Optional[Dict[str, Any]]: ...
    
    # Медиа
    async def download_media(self, channel: Channel, attachment: UnifiedAttachment) -> bytes: ...
    
    # Утилиты
    def format_outgoing(self, response: UnifiedResponse, constraints: ChannelResponseConstraints) -> List[Dict[str, Any]]: ...
    def validate_outgoing(self, response: UnifiedResponse, constraints: ChannelResponseConstraints) -> None: ...
```

---

## 5.5. Generic Webhook Endpoint

```python
# api/v1/webhooks/generic.py (схематичный контракт; ACK адаптируется под provider)
from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import ValidationError
from json import JSONDecodeError
from ai_seller_core.adapters.registry import AdapterRegistry
from ai_seller_core.channels.service import ChannelService
from ai_seller_core.inbox.service import InboxService

router = APIRouter()

@router.post("/webhooks/{channel_type}/{channel_id}")
async def generic_webhook(
    channel_type: str,
    channel_id: str,
    request: Request,
    registry: AdapterRegistry = Depends(),
    channel_service: ChannelService = Depends(),
    inbox_service: InboxService = Depends(),
):
    # 1. Найти канал и проверить соответствие маршрута подключения
    channel = await channel_service.get_by_id(channel_id)
    if not channel or channel.type != channel_type:
        raise HTTPException(404, "Channel not found")

    # 2. Получить адаптер и проверить источник до разбора события
    adapter = registry.get(channel_type)
    if not adapter:
        raise HTTPException(501, f"No adapter for channel type: {channel_type}")

    if not await adapter.verify_webhook(request, channel):
        raise HTTPException(401, "Invalid webhook signature")

    raw_body = await request.body()
    headers = dict(request.headers)

    # Специальный provider handshake/ACK оформляется адаптером отдельным маршрутом.
    try:
        raw_payload = adapter.decode_payload(raw_body, headers)
        events = await adapter.parse_inbound(raw_payload, headers)
    except JSONDecodeError:
        logger.warning("Invalid webhook JSON", extra={"channel_id": channel_id, "channel_type": channel_type})
        raise HTTPException(400, "Invalid JSON payload")
    except ValidationError:
        logger.warning("Invalid webhook payload", extra={"channel_id": channel_id, "channel_type": channel_type})
        raise HTTPException(400, "Invalid payload format")

    # Inbox insert и durable jobs создаются атомарно; повтор provider_event_id
    # подтверждается без повторного запуска обработчика.
    await inbox_service.persist_events_and_jobs(channel, events)

    return adapter.webhook_ack()
```

`persist_events_and_jobs` подтверждает каждое событие по уникальному `(channel_id, provider_event_id)` и в одной PostgreSQL-транзакции создаёт только необходимые jobs. In-memory `BackgroundTasks`/fire-and-forget после ответа не считается надёжной обработкой. Endpoint отвечает только после commit и возвращает точный body/status, требуемый конкретным provider; универсальный `200 {"ok": true}` не навязывается всем интеграциям. События без стабильного provider ID дедуплицируются по определённому в адаптере стабильному ключу; если ключ сформировать невозможно, это ограничение явно логируется, а не маскируется как гарантированная идемпотентность.

---

## 5.6. SessionResolver стратегии

```python
# adapters/session_strategy.py

class MaxSessionResolver(SessionResolver):
    # Event Router вызывает resolver только для event_type=message/callback
    # и передаёт вложенный MessagePayload.
    def resolve_session_id(self, inbound: MessagePayload) -> str:
        # MAX: dialog -> 1 сессия, chat -> сессия на пользователя
        chat_type = inbound.metadata.channel_specific.get("chat_type", "dialog")
        if chat_type == "dialog":
            return f"max:dialog:{inbound.metadata.channel_specific['chat_id']}"
        return f"max:chat:{inbound.metadata.channel_specific['chat_id']}:{inbound.customer_id}"
    
    def resolve_customer_id(self, inbound: MessagePayload) -> str:
        return f"max:{inbound.metadata.author_id}"

class OzonSessionResolver(SessionResolver):
    def resolve_session_id(self, inbound: MessagePayload) -> str:
        # Озон: 1 чат = 1 сессия (привязан к posting_number)
        return f"ozon:chat:{inbound.metadata.channel_specific['chat_id']}"
    
    def resolve_customer_id(self, inbound: MessagePayload) -> str:
        return f"ozon:customer:{inbound.metadata.author_id}"
    
    def extract_context(self, inbound: MessagePayload) -> Dict[str, Any]:
        return {
            "posting_number": inbound.metadata.channel_specific.get("posting_number"),
            "chat_type": inbound.metadata.chat_type,
            "sla_window": inbound.metadata.channel_specific.get("sla_window"),
        }

class AvitoSessionResolver(SessionResolver):
    def resolve_session_id(self, inbound: MessagePayload) -> str:
        return f"avito:chat:{inbound.metadata.channel_specific['chat_id']}"
    
    def resolve_customer_id(self, inbound: MessagePayload) -> str:
        return f"avito:customer:{inbound.metadata.author_id}"
    
    def extract_context(self, inbound: MessagePayload) -> Dict[str, Any]:
        # Контекст объявления из Chat.context.value
        return inbound.metadata.item_context or {}

class Bitrix24CrmSessionResolver(SessionResolver):
    def resolve_session_id(self, inbound: MessagePayload) -> str:
        entity_type = inbound.metadata.channel_specific.get("entity_type", "lead")
        entity_id = inbound.metadata.channel_specific.get("entity_id")
        return f"bitrix24:crm:{entity_type}:{entity_id}"
    
    def resolve_customer_id(self, inbound: MessagePayload) -> str:
        # Резолвим контакт/компанию через CRM
        contact_id = inbound.metadata.channel_specific.get("contact_id")
        if contact_id:
            return f"bitrix24:contact:{contact_id}"
        company_id = inbound.metadata.channel_specific.get("company_id")
        if company_id:
            return f"bitrix24:company:{company_id}"
        return f"bitrix24:user:{inbound.metadata.author_id}"
    
    def extract_context(self, inbound: MessagePayload) -> Dict[str, Any]:
        return inbound.metadata.item_context or {}
```

---

## 5.7. CredentialProvider + TokenManager

```python
# adapters/credential.py

class StaticTokenCredentialProvider(CredentialProvider):
    """MAX: простой статический токен"""
    async def get_credentials(self, channel: Channel) -> Dict[str, Any]:
        return {"bot_token": channel.credentials["bot_token"]}

class ApiKeyPairCredentialProvider(CredentialProvider):
    """Ozon: Client-Id + Api-Key"""
    async def get_credentials(self, channel: Channel) -> Dict[str, Any]:
        return {
            "client_id": channel.credentials["client_id"],
            "api_key": channel.credentials["api_key"],
        }

class OAuth2ClientCredentialsProvider(CredentialProvider, TokenManager):
    """Avito, Bitrix24 (OAuth приложения): Client Credentials + Refresh"""
    
    async def get_credentials(self, channel: Channel) -> Dict[str, Any]:
        token = await self.get_valid_token(channel)
        return {"access_token": token}
    
    async def get_valid_token(self, channel: Channel) -> str:
        creds = channel.credentials
        if creds.get("expires_at", 0) > time.time() + 60:
            return creds["access_token"]
        return await self.refresh_token(channel)
    
    async def refresh_token(self, channel: Channel) -> str:
        # POST /token с grant_type=refresh_token
        # Обновить channel.credentials
        pass

class WebhookSecretCredentialProvider(CredentialProvider):
    """Верификация вебхука по секрету"""
    async def verify(self, channel: Channel, request: Request) -> bool:
        secret = channel.credentials.get("webhook_secret")
        if not secret:
            return True  # нет секрета — пропускаем
        # Проверка подписи (HMAC, JWT, custom header)
        pass
```

---

## 5.8. MediaUploader стратегии

```python
# adapters/media.py

class MaxMediaUploader(MediaUploader):
    """3 шага: get_upload_url -> upload file -> token"""
    async def upload(self, channel: Channel, file: bytes, media_type: str, filename: str) -> str:
        token = await self._three_step_upload(channel, file, media_type)
        return token

class OzonMediaUploader(MediaUploader):
    """Не поддерживается"""
    async def upload(self, channel: Channel, file: bytes, media_type: str, filename: str) -> str:
        raise NotImplementedError("Ozon Chat doesn't support media")

class AvitoMediaUploader(MediaUploader):
    """2 шага: uploadImages -> image_id -> send"""
    async def upload(self, channel: Channel, file: bytes, media_type: str, filename: str) -> str:
        if media_type != "image":
            raise NotImplementedError("Avito only supports images")
        return await self._two_step_upload(channel, file)

class Bitrix24MediaUploader(MediaUploader):
    """Disk API: uploadfile -> file_id -> attach"""
    async def upload(self, channel: Channel, file: bytes, media_type: str, filename: str) -> str:
        return await self._disk_upload(channel, file, filename)
```

---

## 5.9. Capability-Based Response Formatting

До вызова модели runtime формирует `ChannelResponseConstraints` для текущего канала и сессии. Например, для Ozon Chat модель получает режим только plain text, лимит длины ответа и оставшееся SLA-окно; для канала с карточками/кнопками эти формы разрешаются только при фактически включённых capabilities. Ограничения передаются в system context/structured-output schema. После генерации Response Validator повторно проверяет длину, типы элементов и дедлайн. Если ответ нарушает требования, runtime может сделать ограниченный запрос на текстовую коррекцию у того же единственного provider либо сформировать безопасный кодовый ответ; это не provider fallback. Произвольное усечение, способное удалить цену/условия или изменить смысл, запрещено.

```python
# adapters/converter.py

class CapabilityConverter:
    """Конвертер UnifiedResponse -> канал-специфичный формат с graceful degradation"""
    
    def __init__(self, capabilities: ChannelCapabilities):
        self.caps = capabilities
    
    def convert(self, response: UnifiedResponse, constraints: ChannelResponseConstraints) -> List[Dict[str, Any]]:
        """Возвращает список сообщений для отправки (может быть несколько)"""
        validate_response(response, constraints)
        messages = []
        
        # 1. Текст
        text = self._prepare_text(response.text)
        
        # 2. Карточки -> деградация
        card_messages = self._convert_cards(response.cards)
        
        # 3. Клавиатура -> деградация
        keyboard = self._convert_keyboard(response.keyboard)
        
        # 4. Медиа
        media_messages = self._convert_media(response.media)
        
        # Сборка
        if text or keyboard:
            messages.append({"text": text, "keyboard": keyboard, "parse_mode": response.parse_mode})
        messages.extend(card_messages)
        messages.extend(media_messages)
        
        return messages
    
    def _prepare_text(self, text: Optional[str]) -> str:
        if not text:
            return ""
        if len(text) > self.caps.max_message_length:
            raise CapabilityViolation("Response exceeds channel text limit; request correction before delivery")
        return text
    
    def _convert_cards(self, cards: List[UnifiedCard]) -> List[Dict]:
        if not cards or not self.caps.supports_cards:
            # Деградация: карточка -> текст с ссылкой
            return [{"text": self._card_to_text(c)} for c in cards]
        # Нативная поддержка (Avito item, Bitrix product rows)
        return [self._card_to_native(c) for c in cards]
    
    def _convert_keyboard(self, kb: Optional[UnifiedKeyboard]) -> Optional[Dict]:
        if not kb or not self.caps.supports_keyboards:
            return None
        # Фильтруем кнопки по возможностям канала
        filtered = []
        for row in kb.buttons:
            filtered_row = []
            for btn in row:
                if btn.type == "callback" and not self.caps.supports_callback_buttons:
                    # Деградация: callback -> текст с инструкцией
                    filtered_row.append({"type": "text", "text": f"{btn.text} (напишите: {btn.payload})"})
                elif btn.type == "url" and not self.caps.supports_url_buttons:
                    filtered_row.append({"type": "text", "text": f"{btn.text}: {btn.url}"})
                else:
                    filtered_row.append(btn.model_dump(exclude_none=True))
            if filtered_row:
                filtered.append(filtered_row)
        return {"buttons": filtered, "inline": kb.inline} if filtered else None
    
    def _card_to_text(self, card: UnifiedCard) -> str:
        parts = [f"📦 {card.title}"]
        if card.description:
            parts.append(card.description)
        if card.price:
            parts.append(f"💰 {card.price} {card.currency or ''}")
        if card.item_url:
            parts.append(f"🔗 {card.item_url}")
        return "\n".join(parts)
```

---

## 5.10. RateLimiter конфигурация

```python
# adapters/rate_limiter.py

RATE_LIMIT_CONFIG = {
    "max": {"rps": 30, "burst": 50, "strategy": "token_bucket"},
    "ozon_chat": {"rps": 10, "burst": 20, "strategy": "token_bucket"},
    "avito_messenger": {"rps": 30, "burst": 50, "strategy": "sliding_window"},
    "bitrix24_crm": {"rps": 50, "burst": 100, "strategy": "token_bucket"},
    "bitrix24_im": {"rps": 50, "burst": 100, "strategy": "token_bucket"},
    "bitrix24_tasks": {"rps": 50, "burst": 100, "strategy": "token_bucket"},
    "telegram": {"rps": 30, "burst": 50, "strategy": "token_bucket"},
    "whatsapp": {"rps": 20, "burst": 30, "strategy": "token_bucket"},
    "vk": {"rps": 3, "burst": 10, "strategy": "sliding_window"},
    # Wildberries — точка роста. Не задавать предполагаемый лимит до проверки API и отдельной спецификации.
    "yandex_market": {"rps": 10, "burst": 20, "strategy": "token_bucket"},
    "moysklad": {"rps": 50, "burst": 100, "strategy": "token_bucket"},
    "1c": {"rps": 5, "burst": 10, "strategy": "sliding_window"},
    "amocrm": {"rps": 7, "burst": 15, "strategy": "token_bucket"},
}

class RedisTokenBucketRateLimiter(RateLimiter):
    """Опциональная реализация; основная работа MVP не зависит от Redis."""
    def __init__(self, redis_client):
        self.redis = redis_client
    
    async def acquire(self, channel_type: str, tokens: int = 1):
        config = RATE_LIMIT_CONFIG.get(channel_type, {"rps": 10, "burst": 20})
        key = f"ratelimit:{channel_type}"
        # Lua script для атомарности
        ...
```

---

## 5.11. IdempotencyManager

```python
# adapters/idempotency.py

class IdempotencyManager:
    # Входящие события: уникальный индекс PostgreSQL
    async def ingest(self, channel_id: str, event: UnifiedEvent) -> IngestResult:
        # Одна транзакция:
        # INSERT inbox(channel_id, provider_event_id, ...) ON CONFLICT DO NOTHING
        # если вставлено: INSERT background_jobs(...)
        # если конфликт: вернуть duplicate без создания job
        ...

    # Исходящие операции имеют собственный ключ, стабильный между повторами.
    def outgoing_key(self, channel_id: str, operation: str, aggregate_id: str) -> str:
        return f"{channel_id}:{operation}:{aggregate_id}"
```

Не использовать hash полного входящего payload как основной ключ дедупликации: однотипные легитимные события могут иметь одинаковое тело, а несущественные изменения payload дадут другой хеш. При наличии provider event ID он обязателен. Для исходящих вызовов ключ формируется из стабильного внутреннего ID сообщения/заявки и операции, а не из случайного UUID на каждую попытку.

---

## 5.12. Регистрация адаптера (пример для нового канала)

```python
# adapters/telegram/adapter.py
from ai_seller_core.adapters.base import ChannelAdapter
from ai_seller_core.core.schemas.unified import ChannelCapabilities, MessageType

class TelegramAdapter(ChannelAdapter):
    channel_type = "telegram"
    
    capabilities = ChannelCapabilities(
        max_message_length=4096,
        supports_formatting=True,
        supported_formats=["markdown", "html"],
        supports_outgoing_media=True,
        supported_media_types=[MessageType.IMAGE, MessageType.VIDEO, MessageType.AUDIO, MessageType.FILE, MessageType.STICKER, MessageType.LOCATION, MessageType.CONTACT],
        max_file_size_mb=50,
        supports_cards=False,
        supports_keyboards=True,
        supports_inline_keyboards=True,
        supports_callback_buttons=True,
        supports_url_buttons=True,
        supports_contact_request=True,
        supports_location_request=True,
        supports_web_app_buttons=True,
        callback_payload_max_length=64,
        supports_webhook=True,
        supports_polling=True,
        webhook_timeout_seconds=30,
        webhook_requires_443=True,
        webhook_signature_header="X-Telegram-Bot-Api-Secret-Token",
        session_per_user_in_group=True,
        supports_typing_indicator=True,
        supports_message_edit=True,
        supports_message_delete=True,
        delete_window_minutes=48,
        rate_limit_rps=30,
        rate_limit_burst=50,
        auth_type="static_token",
        required_scopes=[],
        has_native_item_context=False,
    )
    
    async def parse_inbound(self, raw_payload: Dict, headers: Dict) -> List[UnifiedEvent]:
        # Парсинг Telegram Update -> типизированные события, включая non-message updates
        ...
    
    async def verify_webhook(self, request: Request, channel: Channel) -> bool:
        # Проверка X-Telegram-Bot-Api-Secret-Token
        ...
    
    async def send(self, channel: Channel, session_id: str, response: UnifiedResponse) -> Dict:
        # Отправка через Bot API (sendMessage, sendPhoto, etc.)
        ...
    
    async def register_webhook(self, channel: Channel, base_url: str) -> Dict:
        # setWebhook с secret_token
        ...
    
    def format_outgoing(self, response: UnifiedResponse) -> List[Dict]:
        return CapabilityConverter(self.capabilities).convert(response)
```

---

## 5.13. Тестирование (ChannelTestHarness)

```python
# adapters/testing/harness.py

class ChannelTestHarness:
    """Базовый класс для тестов адаптеров. Наследуется каждым адаптером."""
    
    adapter_class: type[ChannelAdapter]
    channel_type: str
    
    # Фикстуры (переопределяются в наследнике)
    inbound_fixtures: List[Dict] = []      # сырые вебхуки
    expected_unified: List[UnifiedEvent] = []
    outbound_cases: List[Tuple[UnifiedResponse, List[Dict]]] = []  # (Unified, expected native)
    
    @pytest.fixture
    def adapter(self):
        return self.adapter_class()
    
    @pytest.mark.parametrize("raw,expected", zip(inbound_fixtures, expected_unified))
    async def test_parse_inbound(self, adapter, raw, expected):
        result = await adapter.parse_inbound(raw, {})
        assert result == expected
    
    @pytest.mark.parametrize("unified,expected_native", outbound_cases)
    async def test_format_outbound(self, adapter, unified, expected_native):
        result = adapter.format_outgoing(unified)
        assert result == expected_native
    
    async def test_roundtrip(self, adapter):
        # Unified -> Native -> Unified
        for unified in self.expected_unified:
            native = adapter.format_outgoing(unified)
            # Мок отправки и получения ответа
            parsed = await adapter.parse_inbound(mock_native_response(native), {})
            assert parsed[0].message.text == unified.message.text
    
    async def test_webhook_verification(self, adapter):
        # Валидный и невалидный секреты
        ...
    
    async def test_rate_limiting(self, adapter):
        # Проверка токен-бакета
        ...
    
    async def test_media_upload(self, adapter):
        # Загрузка разных типов медиа
        ...
    
    async def test_idempotency(self, adapter):
        # Повторный запрос с тем же ключом
        ...
```

---

## 5.14. Этапность адаптеров (справочно; не план MVP-1)

| Адаптер | Статус | Что нужно сделать |
|---------|--------|-------------------|
| **MAX** | MVP-1 | Реализовать MAX-адаптер и требуемые общие контракты согласно ТЗ |
| **Ozon Chat** | Будущий этап | После MVP-1 отдельно проверить официальный API и требования |
| **Avito Messenger** | Будущий этап | После MVP-1 отдельно проверить официальный API и требования |
| **Bitrix24** | Будущий этап | CRM/IM/Tasks не входят в MVP-1 |

---

## 5.15. Возможная последовательность дальнейшего развития (не план/оценка MVP-1)

| Этап | Задачи | Оценка |
|------|--------|--------|
| **1. Core Contracts** | `unified.py`, `ChannelCapabilities`, `ChannelAdapter` Protocol | 2 дня |
| **2. Infrastructure** | `AdapterRegistry`, `GenericWebhookEndpoint`, `CredentialProvider`, `TokenManager` | 3 дня |
| **3. Session & Context** | `SessionResolver` базовые стратегии, `ContextExtractor` | 2 дня |
| **4. Media & Rate Limit** | `MediaUploader` стратегии, `RateLimiter` (Redis опционален), `IdempotencyManager` | 3 дня |
| **5. Converter** | `CapabilityConverter` с graceful degradation | 2 дня |
| **6. Testing Harness** | `ChannelTestHarness`, фикстуры для 4 каналов | 3 дня |
| **7. Migration** | Рефакторинг MAX, Ozon, Avito, Bitrix24 под новый фреймворк | 5 дней |
| **8. New Channels** | Telegram, WhatsApp, VK как proof-of-concept | 3 дня/канал |
| **Total** | | **~23 дня** |

---

## 5.16. Чек-лист расширения (не критерии готовности MVP-1)

- [ ] `ChannelAdapter` Protocol покрывает операции подключаемого канала
- [ ] `UnifiedEvent` представляет message, callback и немесседжные события без фиктивного пустого текста
- [ ] `GenericWebhookEndpoint` обрабатывает вебхуки MAX; дополнительные адаптеры — только на будущих этапах
- [ ] Event Router не запускает Dialog Runtime для CRM/lifecycle/status events без соответствующего сценария
- [ ] Inbox обеспечивает долговременную дедупликацию, Outbox — повторную отправку с проверкой срока актуальности
- [ ] `CapabilityConverter` корректно деградирует: карточки→текст, кнопки→текст, медиа→skip
- [ ] Ограничения ответа передаются модели до генерации и проверяются сервером после генерации
- [ ] `SessionResolver` правильно резолвит session_id для всех типов чатов
- [ ] `ContextExtractor` извлекает item_context у Avito и Bitrix24
- [ ] `TokenManager` обновляет OAuth токены при 401
- [ ] Rate limiting не зависит от доступности Redis; Redis опционален, PostgreSQL polling durable jobs продолжается без него
- [ ] Каналы различают `healthy/degraded/failed/requires_setup/disabled` и домен причины
- [ ] `ChannelTestHarness` запускается для MAX; другие адаптеры тестируются при подключении на будущих этапах
- [ ] Документация для команды: как добавить адаптер с учётом особенностей его API

---

## 5.17. Будущий пример добавления канала (Telegram; не MVP-1)

```bash
# 1. Создать структуру
mkdir -p ai_seller_core/adapters/telegram

# 2. Реализовать adapter.py (наследует ChannelAdapter)
#    - parse_inbound (Update -> List[UnifiedEvent])
#    - format_outgoing (UnifiedResponse -> Bot API calls)
#    - verify_webhook (secret_token)
#    - register_webhook (setWebhook)
#    - send_typing (sendChatAction)

# 3. Добавить в registry.py
from ai_seller_core.adapters.telegram import TelegramAdapter
AdapterRegistry.register("telegram", TelegramAdapter())

# 4. Добавить конфиг rate limits в rate_limiter.py

# 5. Написать тесты (наследуем ChannelTestHarness)
#    - fixtures: message, edited_message, callback_query, inline_query
#    - outbound: text, photo, document, inline_keyboard

# 6. Добавить в ChannelCapabilities таблицу БД (миграция)

# Готово! Канал работает через GenericWebhookEndpoint.
```

---

## 5.18. Связь с core-architecture.md (F31)

> **F31. Generic Channel Integration Framework**
>
> Единый фреймворк для внешних каналов. Состоит из:
> - `ChannelAdapter` Protocol и `AdapterRegistry` — контракты и выбор адаптера
> - `UnifiedEvent` / `UnifiedResponse` — канонические события и ответы
> - `Event Router` — маршрутизация событий к диалогу, CRM и lifecycle handlers
> - `ChannelCapabilities` / `ChannelResponseConstraints` — ограничения до генерации и валидация после
> - `GenericWebhookEndpoint`, durable Inbox/jobs и Outbox — приём, дедупликация и доставка с повторами
> - `SessionResolver` и `ContextExtractor` — идентификация диалога и извлечение контекста (F30)
> - `CredentialProvider` + `TokenManager`, `RateLimiter`, `Channel Health Monitor`
> - `ChannelTestHarness` — общие контрактные тесты адаптеров
>
> **Результат:** новые интеграции используют повторно проверенные контракты и инфраструктуру; оценка срока и объёма определяется отдельно по API и сценариям конкретного сервиса.

---

*Документ обновляется при добавлении новых интеграций. Возможные следующие направления: Telegram Bot API, VK Bot API, WhatsApp Business API, Яндекс.Маркет API, 1С, МойСклад, CRM (AmoCRM). Wildberries остаётся точкой роста: его транспорт, лимиты и объём операций будут определены только после отдельного изучения официального API.*