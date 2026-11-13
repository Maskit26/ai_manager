# AI Manager — Project Index

> Auto-generated index for AI-assisted navigation. Updated: 2026-11-09 (session lifecycle rules added)

---

## 📁 Repository Structure

```
ai_manager/
├── docs/                          # Documentation (source of truth: BACKEND_MVP_TZ.md)
│   ├── BACKEND_MVP_TZ.md          # 🔴 SINGLE SOURCE OF TRUTH — MVP requirements, data model, API, security, acceptance criteria
│   ├── core-architecture.md       # Architecture boundaries, components, data ownership, LLM boundary, PII flow, deployment
│   ├── SERVICE_BLOCK_DIAGRAM.md   # Mermaid diagrams: component topology, event flow, session lifecycle, outbox, roles
│   ├── EXTERNAL_INTEGRATIONS.md   # Integration contracts: MAX Messenger (MVP-1), future channels/CRM/LLM providers
│   └── README.md                  # Documentation index + target repo structure with [есть]/[частично]/[план] markers
├── migrations/                    # Alembic migrations (offline SQL)
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       ├── 0001_initial_durable_processing.py
│       └── 7612d753d48f_add_session_lifecycle_fields_and_chat_.py
├── src/ai_seller_core/            # Backend scaffold (FastAPI, Python 3.12, SQLAlchemy 2.0, uv)
│   ├── __init__.py
│   ├── main.py                    # FastAPI app factory, lifespan, routers
│   ├── api/
│   │   ├── __init__.py
│   │   └── health.py              # GET /healthz, /readyz
│   ├── core/
│   │   ├── __init__.py
│   │   └── config.py              # Pydantic Settings from .env (DB, Redis, MAX, LLM, worker, PII)
│   ├── dialog/
│   │   ├── __init__.py
│   │   ├── runtime.py             # Stateless DialogRuntime contract and LLM/tool loop
│   │   └── session_service.py     # Session open/resume/close and fenced turn commit
│   ├── jobs/
│   │   ├── __init__.py
│   │   ├── repository.py          # JobRepository: enqueue, claim_lease, complete, fail, dead_letter, requeue_stalled
│   │   ├── worker.py              # Generic WorkerBase: lease, fence, retry/backoff, dead-letter, shutdown
│   │   ├── inbox.py               # InboxProcessor: idempotent MAX events → DialogRuntime
│   │   ├── inbox_handler.py       # Partially wired dialog handler; requires app adapters/resolvers
│   │   ├── outbox.py              # OutboxDispatcher: pre-flight checks, delivery_unknown on timeout
│   │   ├── delivery.py            # MaxDeliveryAdapter: send_message, mark_read, typing, webhook verification
│   │   └── retry.py               # Exponential backoff + jitter policy
│   └── persistence/
│       ├── __init__.py
│       ├── base.py                # DeclarativeBase, UUID pk, timestamps, async engine/session
│       ├── models.py              # SQLAlchemy models: InboxEvent, OutboxMessage, BackgroundJob, DialogSession, ChatMessage
│       └── session_lease.py       # Atomic session lease acquire/renew/release and fence checks
├── tests/
│   ├── test_health.py             # Health endpoint tests
│   ├── test_dialog_runtime.py     # DialogRuntime contract tests (mock)
│   └── test_retry.py              # Backoff policy tests
├── .env.example                   # Template for environment variables
├── alembic.ini                    # Alembic config (offline SQL generation)
├── pyproject.toml                 # Project metadata, dependencies, tool config (ruff, pytest, mypy)
├── uv.lock                        # Locked dependencies
└── README.md                      # Root readme (brief)
```

---

## 🎯 Key Files by Purpose

| Purpose | File(s) |
|---------|---------|
| **Requirements (normative)** | `docs/BACKEND_MVP_TZ.md` |
| **Architecture decisions** | `docs/core-architecture.md` |
| **Visual diagrams** | `docs/SERVICE_BLOCK_DIAGRAM.md` |
| **Integration specs** | `docs/EXTERNAL_INTEGRATIONS.md` |
| **App entrypoint** | `src/ai_seller_core/main.py` |
| **Configuration** | `src/ai_seller_core/core/config.py` |
| **Dialog runtime contract** | `src/ai_seller_core/dialog/runtime.py` |
| **Session lifecycle / commit** | `src/ai_seller_core/dialog/session_service.py` |
| **Background job framework** | `src/ai_seller_core/jobs/worker.py`, `repository.py`, `retry.py` |
| **Inbox (MAX events)** | `src/ai_seller_core/jobs/inbox.py` |
| **Inbound dialog handler** | `src/ai_seller_core/jobs/inbox_handler.py` |
| **Outbox (commands → MAX)** | `src/ai_seller_core/jobs/outbox.py`, `delivery.py` |
| **Session lease/fencing** | `src/ai_seller_core/persistence/session_lease.py` |
| **Database models** | `src/ai_seller_core/persistence/models.py` |
| **Migrations** | `migrations/versions/0001_initial_durable_processing.py`, `migrations/versions/0002_billing_schema.py` |
| **Billing / Dancoin / Entitlements** | `src/ai_seller_core/billing/` (planned) |

---

## 🔑 MVP-1 Boundaries (from BACKEND_MVP_TZ.md)

- **Channel**: MAX Messenger only (webhook + REST)
- **LLM**: Single provider behind `LLMProvider` interface — no routing/fallback in MVP-1
- **Catalog**: CSV/XLSX import from single authoritative source
- **Orders**: Internal applications only (no CRM in MVP-1), created after explicit buyer confirmation
- **PII**: Mandatory form before AI chat → DB/CRM; free-text PII masked/deleted before storage & LLM
- **Storage**: PostgreSQL = source of truth (business state, Inbox, durable jobs, Outbox)
- **Vector search**: Qdrant = derived index (semantic), commercial fields verified from PG
- **Redis**: Optional (polling from PG works); not required for MVP-1
- **Deployment**: Modular monolith, systemd on Linux, no containers

---

## 🧱 Core Domain Models (persistence/models.py)

| Model | Purpose |
|-------|---------|
| `InboxEvent` | Immutable incoming MAX events (idempotency via `event_id`) |
| `OutboxCommand` | Commands to send to MAX (status, retries, `delivery_unknown` on timeout) |
| `Job` | Durable background jobs (payload, lease, fence, retry, dead-letter) |
| `DialogSession` | Buyer session: lease, fence, state (`active`/`closed`), `release_id`, `last_activity_at`, `closed_at`, `close_reason` (`max_chat_closed`/`idle_ttl`/`manual`), `closed_by`, `reopen_count`, escalation, operator assignment |
| `CatalogProduct` | Product master data (authoritative source) |
| `KnowledgeChunk` | RAG chunks with `embedding` (pgvector) + `qdrant_point_id` (derived) |
| `PiiRecord` | PII from mandatory form (never sent to LLM) |
| `Escalation` | Human handoff requests |
| `Operator` / `OperatorAssignment` | Role-based access (owner/admin/operator matrix) |
| `AuditLog` | Immutable audit trail |
| **Billing models** | `TariffPlan`, `LLMModel`, `Subscription`, `DancoinBalance`, `DancoinTransaction`, `Invoice`, `Payment`, `UsageRecord`, `AgentSlot` |

---

## ⚙️ Background Job Patterns

| Pattern | Implementation |
|---------|----------------|
| **Lease + Fence** | `SessionLeaseManager.acquire_lease()` → atomic `UPDATE ... SET lease_fence = lease_fence + 1` |
| **Session Lifecycle** | `open_or_resume_session` / `close_session` / `commit_turn` — only Dialog Worker; `commit_turn` = single fenced transaction for turn results |
| **Idempotent Inbox** | `InboxProcessor` checks `InboxEvent.event_id` unique constraint before processing |
| **Outbox Pre-flight** | `OutboxDispatcher` re-checks session status, escalation, operator pause, deadline before send |
| **Retry Policy** | Exponential backoff + jitter (`retry.py`), max attempts configurable |
| **Dead Letter** | After max retries → `dead_letter` status, alerting hook |
| **Billing Scheduler** | Monthly Dancoin grant, daily extra-agent spend, invoice generation, grace period, entitlements recalc |

---

## 🔌 External Integrations (MVP-1 = MAX only)

| Integration | File | Status |
|-------------|------|--------|
| MAX Messenger Webhook | `jobs/inbox.py`, `jobs/delivery.py` | Scaffolded |
| MAX REST API (send/mark_read/typing) | `jobs/delivery.py` | Scaffolded |
| LLM Provider (OpenAI-compatible) | `dialog/runtime.py` (protocol) | Interface only |
| **ЮKassa (Payment Provider)** | `billing/payment_provider.py` (planned) | Interface only |
| Beget AI Gateway | `EXTERNAL_INTEGRATIONS.md` | Candidate for evaluation |
| Future: Telegram, WhatsApp, Avito, CRM | `EXTERNAL_INTEGRATIONS.md` | Post-MVP-1 |

---

## 🧪 Tests & Quality

```bash
# Run tests
uv run pytest -v

# Lint
uv run ruff check .

# Type check
uv run mypy src/

# Generate offline migration SQL
uv run alembic upgrade head --sql
```

Current: 6 tests passing, Ruff clean, offline migration generated (2 migrations: durable processing + billing schema).

---

## 📋 Next Implementation Steps (per BACKEND_MVP_TZ.md Part 2+)

1. **Part 2**: DB schema & migrations (models → Alembic) — add `chat_sessions` fields: `state`, `last_activity_at`, `closed_at`, `close_reason`, `closed_by`, `reopen_count`
2. **Part 3**: Inbox Event Router + Dialog Runtime + Session Lease — implement `open_or_resume_session`, `close_session`, `commit_turn` in worker
3. **Part 4**: API Contract (OpenAPI) — *agree before coding*
4. **Part 5**: Outbox Dispatcher + MAX Delivery Adapter
5. **Part 6**: Catalog Import (CSV/XLSX) + Qdrant Sync
6. **Part 7**: PII Form + Masking Pipeline
7. **Part 8**: Escalation + Operator Assignment (RBAC)
8. **Part 9**: Observability (structured logs, metrics, health)
9. **Part 10**: Acceptance Tests + Load Test

---

## 🔍 Quick Navigation Tips for AI

- **Start here**: `docs/BACKEND_MVP_TZ.md` — all normative requirements
- **Architecture context**: `docs/core-architecture.md` — component boundaries & data flow
- **Visual reference**: `docs/SERVICE_BLOCK_DIAGRAM.md` — Mermaid diagrams
- **Integration details**: `docs/EXTERNAL_INTEGRATIONS.md` — MAX spec + future
- **Code entry**: `src/ai_seller_core/main.py` → routers → domain modules
- **Database**: `src/ai_seller_core/persistence/models.py` + migration SQL — `DialogSession` needs lifecycle fields
- **Background jobs**: `src/ai_seller_core/jobs/worker.py` (base) → `inbox.py`/`outbox.py` (concrete) — worker owns session lifecycle
- **Session safety**: `src/ai_seller_core/persistence/session_lease.py` (fence token pattern) — used by `commit_turn`