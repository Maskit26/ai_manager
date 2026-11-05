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
uv run alembic upgrade head
uv run uvicorn ai_seller_core.main:app --reload
```

Проверки:

```powershell
uv run pytest
```

API: `http://127.0.0.1:8000/health`  
OpenAPI UI: `http://127.0.0.1:8000/docs`

Фоновый `Worker` запускает обработчики зарегистрированных типов задач из PostgreSQL. До регистрации конкретных обработчиков MAX/LLM/каталога worker-профили не готовы к эксплуатации; незарегистрированная задача не выполняется и после ограниченных попыток попадает в dead-letter.

## Документация

- [ТЗ backend — источник истины](docs/BACKEND_MVP_TZ.md)
- [Архитектурные решения](docs/core-architecture.md)
- [Внешние интеграции](docs/EXTERNAL_INTEGRATIONS.md)
- [Диаграмма сервиса](docs/SERVICE_BLOCK_DIAGRAM.md)
- [Индекс документации](docs/README.md)
