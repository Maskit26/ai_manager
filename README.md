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

`LOG_LEVEL` задаёт уровень логов (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`), а `LOG_FORMAT` — общий формат (`json` или `text`). Одинаковые настройки применяются к API и worker; логи содержат тип процесса и безопасные идентификаторы задач, но не payload или credentials.

## Локальный запуск (Windows / PowerShell)

Установите `uv` и Python 3.12 (если Python ещё не установлен):

```powershell
winget install --id astral-sh.uv -e
uv python install 3.12
```

Из корня репозитория подготовьте окружение и настройки:

```powershell
uv sync --frozen
Copy-Item .env.example .env
```

Отредактируйте `.env`: укажите рабочий `DATABASE_URL` для PostgreSQL. Не коммитьте `.env` и не оставляйте в нём примерные пароли. Для локального профиля Qdrant можно не запускать; тогда `/health` будет отвечать, а `/ready` сообщит `not_configured` и вернёт HTTP 503.

Примените миграции и запустите процессы в отдельных терминалах:

```powershell
uv run alembic upgrade head
uv run uvicorn ai_seller_core.main:app --host 127.0.0.1 --port 8000 --reload
```

```powershell
uv run ai-seller-worker --queue inbound_dialog
```

Остановите каждый процесс отдельно сочетанием `Ctrl+C`. Проверьте `/health` (liveness), `/ready` (PostgreSQL и Qdrant) и `/docs`:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/health
Invoke-WebRequest http://127.0.0.1:8000/ready
Start-Process http://127.0.0.1:8000/docs
```

## Установка на Linux-сервер без контейнеров

Ниже приведён пример для Ubuntu 24.04 с `systemd`. Используйте поддерживаемые версии PostgreSQL и Qdrant и не публикуйте их порты в интернет.

1. Установите системный Python 3.12 и `uv`. Приложение использует системную установку Python, чтобы служебный пользователь и systemd не зависели от Python-файлов в домашнем каталоге администратора.

   ```sh
   sudo apt update
   sudo apt install -y python3.12 python3.12-venv curl
   curl -LsSf https://astral.sh/uv/install.sh | sh
   export PATH="$HOME/.local/bin:$PATH"
   uv --version
   ```

   Не запускайте приложение от `root`.
2. Установите PostgreSQL как системную службу и создайте отдельные роль и базу приложения. Например, на Debian/Ubuntu:

   ```sh
   sudo apt update
   sudo apt install postgresql
   sudo -u postgres createuser --pwprompt ai_seller
   sudo -u postgres createdb --owner=ai_seller ai_seller
   ```

   Разрешите подключение только с узла приложения/локального интерфейса и используйте отдельный сильный пароль для `DATABASE_URL`.
3. Установите Qdrant как отдельную системную службу по [официальной инструкции Qdrant](https://qdrant.tech/documentation/guides/installation/). Ограничьте сетевой доступ к нему; задайте `QDRANT_URL` и, если включена аутентификация, `QDRANT_API_KEY`.
4. Создайте системного пользователя и разместите проверенный релиз приложения в `/opt/ai-seller-core`. Код и виртуальное окружение должны быть доступны на чтение/исполнение служебному пользователю:

   ```sh
   sudo useradd --system --user-group --home-dir /opt/ai-seller-core \
     --no-create-home --shell /usr/sbin/nologin ai-seller
   sudo install -d -o root -g ai-seller -m 0750 /opt/ai-seller-core
   ```

   Из каталога релиза установите зафиксированные зависимости:

   ```sh
   cd /opt/ai-seller-core
   uv sync --frozen --no-dev --python /usr/bin/python3.12
   ```

5. Храните настройки вне репозитория, например в `/etc/ai-seller-core/app.env`:

   ```sh
   sudo install -d -o root -g ai-seller -m 0750 /etc/ai-seller-core
   sudo install -o root -g ai-seller -m 0640 /dev/null /etc/ai-seller-core/app.env
   sudoedit /etc/ai-seller-core/app.env
   ```

   Не помещайте секреты в unit-файлы, командную строку или Git. Укажите как минимум `APP_ENV=production` и `DATABASE_URL`; для production также обязательны `QDRANT_URL` и `SECRET_ENCRYPTION_KEY`. `QDRANT_API_KEY` задавайте только при включённой аутентификации. Требования к формату ключа шифрования должны быть согласованы с реализацией шифрования; не используйте для него пароль БД или фиктивное значение. После редактирования проверьте владельца/права: файл должен принадлежать `root:ai-seller` с режимом `0640`, каталог — иметь режим `0750`.

6. До запуска служб примените миграции, загрузив переменные из защищённого файла:

   ```sh
   cd /opt/ai-seller-core
   uv run --frozen --no-sync --env-file /etc/ai-seller-core/app.env alembic upgrade head
   ```

7. Создайте `/etc/systemd/system/ai-seller-api.service` (например, через `sudoedit /etc/systemd/system/ai-seller-api.service`):

   ```ini
   [Unit]
   Description=AI Seller Core API
   Wants=network-online.target
   After=network-online.target

   [Service]
   Type=simple
   User=ai-seller
   Group=ai-seller
   WorkingDirectory=/opt/ai-seller-core
   EnvironmentFile=/etc/ai-seller-core/app.env
   ExecStart=/opt/ai-seller-core/.venv/bin/uvicorn ai_seller_core.main:app --host 127.0.0.1 --port 8000
   Restart=on-failure
   RestartSec=5
   NoNewPrivileges=true
   PrivateTmp=true
   ProtectSystem=strict
   ProtectHome=true
   UMask=0077

   [Install]
   WantedBy=multi-user.target
   ```

8. Создайте `/etc/systemd/system/ai-seller-worker.service` (через `sudoedit`) с теми же `User`, `Group`, `WorkingDirectory`, `EnvironmentFile` и ограничениями безопасности; замените описание и команду запуска:

   ```ini
   [Unit]
   Description=AI Seller Core Worker
   Wants=network-online.target
   After=network-online.target

   [Service]
   Type=simple
   User=ai-seller
   Group=ai-seller
   WorkingDirectory=/opt/ai-seller-core
   EnvironmentFile=/etc/ai-seller-core/app.env
   ExecStart=/opt/ai-seller-core/.venv/bin/ai-seller-worker --queue inbound_dialog
   Restart=on-failure
   RestartSec=5
   NoNewPrivileges=true
   PrivateTmp=true
   ProtectSystem=strict
   ProtectHome=true
   UMask=0077

   [Install]
   WantedBy=multi-user.target
   ```

   Перечитайте конфигурацию systemd и запустите API и worker как независимые службы:

   ```sh
   sudo systemctl daemon-reload
   sudo systemctl enable --now ai-seller-api.service ai-seller-worker.service
   sudo systemctl status ai-seller-api.service ai-seller-worker.service
   ```

   Для отдельных операций используйте `sudo systemctl restart ai-seller-api`, `sudo systemctl stop ai-seller-api`, `sudo systemctl restart ai-seller-worker` или `sudo systemctl stop ai-seller-worker`. Сбой и перезапуск одного процесса не требуют остановки другого. Просматривайте логи через `sudo journalctl -u ai-seller-api -f` и `sudo journalctl -u ai-seller-worker -f`.

   Ограничьте доступ к порту API локальным интерфейсом и используйте reverse proxy с TLS, если API должен быть доступен извне. После запуска проверьте `curl -i http://127.0.0.1:8000/health` и `curl -i http://127.0.0.1:8000/ready`; `/ready` возвращает 503, если PostgreSQL или настроенный Qdrant недоступны. `/docs` доступен по `http://127.0.0.1:8000/docs`.

### Что требуется для запуска и обработки

- Для старта API и worker обязательна доступная PostgreSQL и корректный `DATABASE_URL`. Qdrant можно не настраивать в локальном профиле, но тогда readiness будет неуспешна; для `staging` и `production` `QDRANT_URL` обязателен.
- Credentials внешнего AI-шлюза не нужны для запуска API, Swagger UI или worker. В текущей версии конкретные обработчики задач и интеграции с AI-шлюзом ещё не зарегистрированы: worker штатно простаивает и не обрабатывает реальные задания. Для реальной обработки сначала потребуются реализация и регистрация обработчиков, а затем credentials/параметры соответствующих интеграций.
- Redis и объектное хранилище для описанного локального и серверного запуска не требуются.

Проверки:

```powershell
uv run pytest
```

Интеграционный тест миграций PostgreSQL запускается только при заданной переменной
`AI_SELLER_TEST_DATABASE_URL`. Укажите адрес **выделенной тестовой базы** с драйвером
`postgresql+asyncpg`; пользователь должен иметь право создавать и удалять схемы.
Тест создаёт и удаляет собственную временную схему. Без переменной тест пропускается.

```powershell
$env:AI_SELLER_TEST_DATABASE_URL = "<адрес выделенной тестовой PostgreSQL>"
uv run pytest tests\test_accounts_projects.py
```

Liveness: `http://127.0.0.1:8000/health`
Readiness (PostgreSQL and Qdrant): `http://127.0.0.1:8000/ready`
OpenAPI UI: `http://127.0.0.1:8000/docs`

`/health` reports that the API process is responsive. `/ready` checks PostgreSQL and Qdrant and returns HTTP 503 unless both are available; an unset Qdrant URL is reported as `not_configured`. The API creates and closes its database engine and Qdrant HTTP client within its lifespan.

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
