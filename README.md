# AI Seller Core

Backend-сервис «ИИ-продавец». Архитектурный источник требований и интеграционные документы находятся в [`docs/`](docs/).

## Текущая основа

- Python 3.12
- FastAPI HTTP API
- Модульный монолит; API и фоновые workers будут запускаться отдельными процессами
- PostgreSQL — источник истины для бизнес-данных и durable jobs, через SQLAlchemy async и Alembic
- Секреты и параметры окружения задаются конфигурацией, не хранятся в репозитории
- Основа Inbox/дедупликации, фоновых jobs с lease/retry/dead-letter, Outbox и session fencing
- Начальный Dialog Runtime с контрактами LLM provider, sanitizer и tool executor

Интеграции MAX, LLM provider, Qdrant и Redis, предметные обработчики диалога, каталога и индексации ещё не подключены. Redis остаётся необязательным ускорителем; durable jobs хранятся в PostgreSQL.

## Запуск локально

Требуется Python 3.12 и `uv`.

```powershell
uv sync --extra dev
Copy-Item .env.example .env
# Укажите в .env доступный локальный PostgreSQL в DATABASE_URL (пример содержит намеренно нерабочий placeholder).
uv run alembic upgrade head
uv run uvicorn ai_seller_core.main:app --reload
```

### Профили конфигурации

`APP_ENV` принимает `development`, `test`, `staging` или `production`. `DATABASE_URL` обязателен для всех профилей и должен использовать драйвер `postgresql+asyncpg`. Для `staging` и `production` также обязательны `QDRANT_URL` и `SECRET_ENCRYPTION_KEY`; формат ключа будет зафиксирован вместе с реализацией шифрования. `QDRANT_API_KEY` задаётся только при включённой аутентификации Qdrant.

В development-профиле адрес Qdrant можно оставить локальным или не задавать. Credentials внешних AI/API-интеграций для запуска приложения не требуются. В production настройте значения через окружение процесса или защищённое хранилище секретов, а не через файл в репозитории.

Проверки:

```powershell
uv run pytest
```

API: `http://127.0.0.1:8000/health`  
OpenAPI UI: `http://127.0.0.1:8000/docs`

API и worker запускаются отдельными процессами и терминалами. Для запуска worker выполните:

```powershell
uv run ai-seller-worker --queue inbound_dialog
```

Worker обрабатывает `SIGINT`/`SIGTERM`, завершает текущую задачу и закрывает пул соединений при остановке. Сейчас конкретные inbox/outbox адаптеры ещё не собраны в исполняемые обработчики, поэтому пустой реестр приводит к штатному ожиданию с предупреждением: worker не забирает и не перемещает задачи в dead-letter. После появления обработчиков они должны быть зарегистрированы в `build_handlers()` в `src/ai_seller_core/jobs/cli.py`. Если реестр не пуст, задача с неизвестным типом по-прежнему считается ошибкой и после ограниченных попыток попадает в dead-letter.

## Документация

- [ТЗ backend — источник истины](docs/BACKEND_MVP_TZ.md)
- [Архитектурные решения](docs/core-architecture.md)
- [Внешние интеграции](docs/EXTERNAL_INTEGRATIONS.md)
- [Диаграмма сервиса](docs/SERVICE_BLOCK_DIAGRAM.md)
- [Индекс документации](docs/README.md)
