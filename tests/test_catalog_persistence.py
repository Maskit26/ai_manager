import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from ai_seller_core.core.config import Settings
from ai_seller_core.dialog.session_service import open_or_resume_session
from ai_seller_core.jobs.inbox import ingest_channel_event
from ai_seller_core.jobs.outbox import enqueue_outbound_message
from ai_seller_core.jobs.repository import enqueue_job
from ai_seller_core.persistence import retention
from ai_seller_core.persistence.access_policy import AccountRole, Principal
from ai_seller_core.persistence.catalog import (
    CatalogImportFinalizationError,
    finalize_catalog_import,
)
from ai_seller_core.persistence.models import (
    Account,
    Agent,
    AgentRelease,
    AuditLog,
    BackgroundJob,
    CatalogImport,
    CatalogImportError,
    CatalogSource,
    Channel,
    Customer,
    CustomerIdentity,
    DomainEvent,
    Escalation,
    InboxEvent,
    KbChunk,
    KbDocument,
    Lead,
    LeadItem,
    LlmCall,
    OutboxMessage,
    Product,
    Project,
    ToolExecution,
    UsageCounter,
    User,
    new_id,
)
from ai_seller_core.persistence.retention import purge_expired_activity


def test_catalog_constraints_and_atomic_finalization_postgresql(monkeypatch) -> None:
    monkeypatch.setattr(
        retention,
        "get_settings",
        lambda: SimpleNamespace(activity_retention_days=30),
    )
    test_database_url = os.environ.pop("DATABASE_URL", None)
    try:
        try:
            database_url = Settings().database_url.get_secret_value()
        except ValidationError as error:
            pytest.skip(f"PostgreSQL integration configuration is unavailable: {error}")
    finally:
        if test_database_url is not None:
            os.environ["DATABASE_URL"] = test_database_url
    if "change-me" in database_url:
        pytest.skip("PostgreSQL integration configuration is a placeholder")

    async def verify() -> None:
        schema = f"catalog_test_{uuid4().hex}"
        engine = create_async_engine(
            database_url,
            pool_size=1,
            max_overflow=0,
            connect_args={"server_settings": {"search_path": schema}},
        )
        try:
            async with engine.begin() as connection:
                await connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
                config = Config("alembic.ini")

                def upgrade(sync_connection) -> None:
                    config.attributes["connection"] = sync_connection
                    command.upgrade(config, "head")

                await connection.run_sync(upgrade)

            async with engine.connect() as connection:
                indexes = {
                    row[0]
                    for row in (
                        await connection.exec_driver_sql(
                            "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()"
                        )
                    )
                }
            assert {
                "ix_catalog_imports_source",
                "ix_catalog_imports_status",
                "ix_catalog_imports_created_at",
                "ix_catalog_import_errors_import",
                "ix_products_sku",
                "ix_products_category",
                "ix_products_activity",
                "ix_background_jobs_status_available_priority",
                "ix_domain_events_project_time_type",
                "ix_domain_events_occurred_at",
                "ix_tool_executions_project_session_message",
                "ix_leads_project_status_created",
                "ix_lead_items_project_lead",
                "ix_escalations_project_status",
                "ix_escalations_project_sla",
                "ix_audit_log_project_time_type",
                "ix_audit_log_account_time_type",
                "ix_audit_log_created_at",
                "ix_llm_calls_account_time",
                "ix_llm_calls_project_time",
                "ix_usage_counters_account_time",
                "ix_usage_counters_project_time",
            } <= indexes

            async with AsyncSession(engine, expire_on_commit=False) as session:
                account_a = Account(status="active")
                account_b = Account(status="active")
                session.add_all((account_a, account_b))
                await session.flush()
                project_a = Project(
                    id=new_id(),
                    account_id=account_a.id,
                    locale="en",
                    timezone="UTC",
                    settings={},
                    status="active",
                )
                project_b = Project(
                    id=new_id(),
                    account_id=account_b.id,
                    locale="en",
                    timezone="UTC",
                    settings={},
                    status="active",
                )
                project_a_id = project_a.id
                project_b_id = project_b.id
                account_a_id = account_a.id
                account_b_id = account_b.id
                session.add_all((project_a, project_b))
                await session.flush()
                channel_id = "web"
                session.add(Channel(project_id=project_a_id, channel_id=channel_id))
                await session.flush()

                first_ingest = await ingest_channel_event(
                    session,
                    project_id=project_a_id,
                    channel_id=channel_id,
                    provider_event_id="provider-event-1",
                    event_type="message",
                    normalized_event={
                        "text": "hello",
                        "api_key": "must-not-be-persisted",
                        "token_count": 3,
                    },
                    correlation_id="inbox-test",
                )
                duplicate_ingest = await ingest_channel_event(
                    session,
                    project_id=project_a_id,
                    channel_id=channel_id,
                    provider_event_id="provider-event-1",
                    event_type="message",
                    normalized_event={"text": "duplicate"},
                    correlation_id="inbox-test-duplicate",
                )
                assert first_ingest.event_id is not None
                assert not first_ingest.duplicate
                assert duplicate_ingest.event_id is None
                assert duplicate_ingest.duplicate
                stored_inbox_event = await session.scalar(
                    select(InboxEvent).where(InboxEvent.id == first_ingest.event_id)
                )
                inbox_jobs_count = await session.scalar(
                    select(func.count())
                    .select_from(BackgroundJob)
                    .where(BackgroundJob.project_id == project_a_id)
                )
                assert stored_inbox_event is not None
                assert stored_inbox_event.status == "queued"
                assert stored_inbox_event.processed_at is None
                assert stored_inbox_event.normalized_event["api_key"] == "[REDACTED]"
                assert stored_inbox_event.normalized_event["token_count"] == 3
                assert inbox_jobs_count == 1
                diagnostic_job = await enqueue_job(
                    session,
                    project_id=project_a_id,
                    queue="maintenance",
                    kind="redaction_test",
                    payload={
                        "client_secret": "must-not-be-persisted",
                        "token_count": 3,
                    },
                    idempotency_key="job-redaction-test",
                    correlation_id="job-redaction-test",
                )
                diagnostic_job.last_error = "token=must-not-be-persisted"
                await session.flush()
                await session.refresh(diagnostic_job)
                assert diagnostic_job.payload["client_secret"] == "[REDACTED]"
                assert diagnostic_job.payload["token_count"] == 3
                assert diagnostic_job.last_error == "token=[REDACTED]"

                agent = Agent(
                    id=new_id(),
                    project_id=project_a_id,
                    status="draft",
                )
                session.add(agent)
                await session.flush()
                release_id = new_id()
                session.add(
                    AgentRelease(
                        id=release_id,
                        project_id=project_a_id,
                        agent_id=agent.id,
                        version=1,
                        snapshot={},
                    )
                )
                await session.flush()
                agent.status = "active"
                agent.active_release_id = release_id
                await session.flush()

                session_context = await open_or_resume_session(
                    session,
                    project_id=project_a_id,
                    channel_id=channel_id,
                    customer_id="customer-for-outbox",
                    external_chat_id="chat-for-outbox",
                    worker_id="catalog-test-worker",
                    lease_seconds=60,
                )
                assert session_context is not None
                outbound = await enqueue_outbound_message(
                    session,
                    project_id=project_a_id,
                    channel_id=channel_id,
                    session_id=session_context.session_id,
                    payload={"api_key": "must-not-be-persisted", "text": "hello"},
                    idempotency_key="outbox-redaction-test",
                    correlation_id="outbox-redaction-test",
                    lease=session_context.lease,
                )
                await session.refresh(outbound)
                assert outbound.payload["api_key"] == "[REDACTED]"
                assert outbound.destination == channel_id
                assert outbound.aggregate_id == session_context.session_id
                outbound.last_error = "authorization: Bearer must-not-be-persisted"
                await session.flush()
                await session.refresh(outbound)
                assert outbound.last_error == "authorization: [REDACTED]"
                outbox_count_before_stale_lease = await session.scalar(
                    select(func.count()).select_from(OutboxMessage)
                )
                with pytest.raises(RuntimeError, match="fenced"):
                    await enqueue_outbound_message(
                        session,
                        project_id=project_a_id,
                        channel_id=channel_id,
                        session_id=session_context.session_id,
                        payload={"text": "must not be inserted"},
                        idempotency_key="outbox-stale-fence-test",
                        correlation_id="outbox-stale-fence-test",
                        lease=replace(
                            session_context.lease,
                            fence=session_context.lease.fence - 1,
                        ),
                    )
                outbox_count_after_stale_lease = await session.scalar(
                    select(func.count()).select_from(OutboxMessage)
                )
                assert outbox_count_after_stale_lease == outbox_count_before_stale_lease

                source = CatalogSource(
                    id=new_id(),
                    project_id=project_a_id,
                    kind="csv",
                    status="active",
                    is_authoritative=True,
                    mapping={},
                )
                source_id = source.id
                session.add(source)
                await session.flush()

                with pytest.raises(IntegrityError):
                    async with session.begin_nested():
                        session.add(
                            CatalogSource(
                                id=new_id(),
                                project_id=project_a_id,
                                kind="xlsx",
                                status="active",
                                is_authoritative=True,
                                mapping={},
                            )
                        )
                        await session.flush()

                with pytest.raises(IntegrityError):
                    async with session.begin_nested():
                        session.add(
                            CatalogImport(
                                id=new_id(),
                                project_id=project_b_id,
                                source_id=source_id,
                                status="pending",
                                total_rows=0,
                                processed_rows=0,
                                error_count=0,
                            )
                        )
                        await session.flush()

                customer_id = new_id()
                customer = Customer(id=customer_id, project_id=project_a_id, profile={})
                document_id = new_id()
                document = KbDocument(
                    id=document_id,
                    project_id=project_a_id,
                    source_key="manual",
                    filename="guide.txt",
                    content="Guide",
                    version=1,
                    status="active",
                )
                session.add_all((customer, document))
                await session.flush()

                tool_execution = ToolExecution(
                    id=new_id(),
                    project_id=project_a_id,
                    session_id=session_context.session_id,
                    tool_name="lookup",
                    status="completed",
                    arguments={"api_key": "must-not-be-persisted"},
                    result={"token": "must-not-be-persisted", "count": 1},
                )
                lead = Lead(
                    id=new_id(),
                    project_id=project_a_id,
                    session_id=session_context.session_id,
                    customer_id=customer_id,
                    idempotency_key="lead-request-1",
                    status="submitted",
                    customer_confirmed=True,
                    consent_given=True,
                    request_data={"authorization": "must-not-be-persisted"},
                )
                escalation = Escalation(
                    id=new_id(),
                    project_id=project_a_id,
                    account_id=account_a.id,
                    session_id=session_context.session_id,
                    reason="human_requested",
                    summary="Customer requested an operator",
                    assignee_membership_id=None,
                    sla_deadline=datetime.now(UTC),
                    status="open",
                )
                await session.flush()
                session.add(User(id="operator-1"))
                audit_entry = AuditLog(
                    id=new_id(),
                    account_id=account_a.id,
                    project_id=project_a_id,
                    actor_id="operator-1",
                    action="lead.created",
                    entity_type="lead",
                    entity_id=lead.id,
                    diff={
                        "password": "must-not-be-persisted",
                        "email": "buyer@example.test",
                        "note": "Call +1 (415) 555-0132",
                    },
                )
                domain_event = DomainEvent(
                    id=new_id(),
                    project_id=project_a_id,
                    session_id=session_context.session_id,
                    event_type="lead.created",
                    aggregate_type="lead",
                    aggregate_id=lead.id,
                    tenant_ids={"account_id": account_a.id},
                    schema_version=1,
                    correlation_id="domain-event-test",
                    payload={"message": "Contact buyer@example.test"},
                )
                expired_at = datetime.now(UTC) - timedelta(days=31)
                retained_at = datetime.now(UTC) - timedelta(days=29)
                expired_audit = AuditLog(
                    id=new_id(),
                    account_id=account_a.id,
                    project_id=project_a_id,
                    action="retention.expired",
                    entity_type="lead",
                    entity_id=lead.id,
                    created_at=expired_at,
                )
                retained_audit = AuditLog(
                    id=new_id(),
                    account_id=account_a.id,
                    project_id=project_a_id,
                    action="retention.retained",
                    entity_type="lead",
                    entity_id=lead.id,
                    created_at=retained_at,
                )
                expired_event = DomainEvent(
                    id=new_id(),
                    project_id=project_a_id,
                    session_id=session_context.session_id,
                    event_type="retention.expired",
                    aggregate_type="lead",
                    aggregate_id=lead.id,
                    tenant_ids={"account_id": account_a.id},
                    occurred_at=expired_at,
                    schema_version=1,
                    correlation_id="retention-expired",
                    payload={},
                )
                retained_event = DomainEvent(
                    id=new_id(),
                    project_id=project_a_id,
                    session_id=session_context.session_id,
                    event_type="retention.retained",
                    aggregate_type="lead",
                    aggregate_id=lead.id,
                    tenant_ids={"account_id": account_a.id},
                    occurred_at=retained_at,
                    schema_version=1,
                    correlation_id="retention-retained",
                    payload={},
                )
                session.add_all((expired_audit, retained_audit, expired_event, retained_event))
                await session.flush()
                assert await purge_expired_activity(session) == (1, 1, 0)
                remaining_event_ids = set(
                    (
                        await session.scalars(
                            select(DomainEvent.id).where(
                                DomainEvent.id.in_((expired_event.id, retained_event.id))
                            )
                        )
                    ).all()
                )
                remaining_audit_ids = set(
                    (
                        await session.scalars(
                            select(AuditLog.id).where(
                                AuditLog.id.in_((expired_audit.id, retained_audit.id))
                            )
                        )
                    ).all()
                )
                assert expired_event.id not in remaining_event_ids
                assert retained_event.id in remaining_event_ids
                assert expired_audit.id not in remaining_audit_ids
                assert retained_audit.id in remaining_audit_ids
                llm_call = LlmCall(
                    id=new_id(),
                    account_id=account_a.id,
                    project_id=project_a_id,
                    provider="gateway",
                    model="test-model",
                    purpose="completion",
                    input_tokens=12,
                    output_tokens=8,
                    latency_ms=20,
                )
                usage_counter = UsageCounter(
                    id=new_id(),
                    account_id=account_a.id,
                    project_id=project_a_id,
                    period_start=datetime.now(UTC),
                    metric="llm_calls",
                    value=1,
                )
                session.add_all(
                    (
                        tool_execution,
                        lead,
                        LeadItem(
                            id=new_id(),
                            project_id=project_a_id,
                            lead_id=lead.id,
                            quantity=1,
                            product_snapshot={"sku": "confirmed-sku", "name": "Snapshot"},
                        ),
                        escalation,
                        audit_entry,
                        domain_event,
                        llm_call,
                        usage_counter,
                    )
                )
                await session.flush()
                await session.refresh(tool_execution)
                await session.refresh(audit_entry)
                await session.refresh(domain_event)
                assert tool_execution.arguments["api_key"] == "[REDACTED]"
                assert tool_execution.result["token"] == "[REDACTED]"
                assert audit_entry.diff["password"] == "[REDACTED]"
                assert audit_entry.diff["email"] == "[REDACTED]"
                assert audit_entry.diff["note"] == "Call [REDACTED_PHONE]"
                assert domain_event.payload["message"] == ("Contact [REDACTED_EMAIL]")

                with pytest.raises(IntegrityError):
                    async with session.begin_nested():
                        session.add(
                            Lead(
                                id=new_id(),
                                project_id=project_a_id,
                                session_id=session_context.session_id,
                                customer_id=customer_id,
                                idempotency_key="lead-request-1",
                                status="submitted",
                            )
                        )
                        await session.flush()
                with pytest.raises(IntegrityError):
                    async with session.begin_nested():
                        session.add(
                            LeadItem(
                                id=new_id(),
                                project_id=project_b_id,
                                lead_id=lead.id,
                                quantity=1,
                                product_snapshot={"sku": "cross-project"},
                            )
                        )
                        await session.flush()
                with pytest.raises(IntegrityError):
                    async with session.begin_nested():
                        session.add(
                            LlmCall(
                                id=new_id(),
                                account_id=account_b_id,
                                project_id=project_a_id,
                                provider="gateway",
                                model="test-model",
                                purpose="completion",
                            )
                        )
                        await session.flush()

                identity = CustomerIdentity(
                    id=new_id(),
                    project_id=project_a_id,
                    customer_id=customer_id,
                    channel_type="max",
                    external_user_id="external-1",
                    verification_status="unverified",
                )
                session.add(identity)
                await session.flush()
                with pytest.raises(IntegrityError):
                    async with session.begin_nested():
                        session.add(
                            CustomerIdentity(
                                id=new_id(),
                                project_id=project_a_id,
                                customer_id=customer_id,
                                channel_type="max",
                                external_user_id="external-1",
                                verification_status="unverified",
                            )
                        )
                        await session.flush()
                with pytest.raises(IntegrityError):
                    async with session.begin_nested():
                        session.add(
                            KbChunk(
                                id=new_id(),
                                project_id=project_b_id,
                                document_id=document_id,
                                chunk_index=0,
                                content="cross-project",
                            )
                        )
                        await session.flush()

                import_record = CatalogImport(
                    id=new_id(),
                    project_id=project_a_id,
                    source_id=source_id,
                    status="processing",
                    total_rows=2,
                    processed_rows=2,
                    error_count=0,
                    source_read_complete=True,
                    structural_error=False,
                    deactivation_safe=True,
                )
                old_product = Product(
                    id=new_id(),
                    project_id=project_a_id,
                    source_id=source_id,
                    external_id="old",
                    name="Old product",
                    attributes={},
                    status="active",
                )
                stale_product = Product(
                    id=new_id(),
                    project_id=project_a_id,
                    source_id=source_id,
                    external_id="stale",
                    name="Stale product",
                    attributes={},
                    status="active",
                )
                unsafe_imports = [
                    CatalogImport(
                        id=new_id(),
                        project_id=project_a_id,
                        source_id=source_id,
                        status="processing",
                        total_rows=2,
                        processed_rows=2,
                        error_count=0,
                        source_read_complete=False,
                        structural_error=False,
                        deactivation_safe=True,
                    ),
                    CatalogImport(
                        id=new_id(),
                        project_id=project_a_id,
                        source_id=source_id,
                        status="processing",
                        total_rows=2,
                        processed_rows=1,
                        error_count=0,
                        source_read_complete=True,
                        structural_error=False,
                        deactivation_safe=True,
                    ),
                    CatalogImport(
                        id=new_id(),
                        project_id=project_a_id,
                        source_id=source_id,
                        status="processing",
                        total_rows=2,
                        processed_rows=2,
                        error_count=0,
                        source_read_complete=True,
                        structural_error=True,
                        deactivation_safe=True,
                    ),
                    CatalogImport(
                        id=new_id(),
                        project_id=project_a_id,
                        source_id=source_id,
                        status="processing",
                        total_rows=2,
                        processed_rows=2,
                        error_count=0,
                        source_read_complete=True,
                        structural_error=False,
                        deactivation_safe=False,
                    ),
                    CatalogImport(
                        id=new_id(),
                        project_id=project_a_id,
                        source_id=source_id,
                        status="processing",
                        total_rows=2,
                        processed_rows=2,
                        error_count=1,
                        source_read_complete=True,
                        structural_error=False,
                        deactivation_safe=True,
                    ),
                ]
                critical_import_id = unsafe_imports[-1].id
                unsafe_import_ids = [record.id for record in unsafe_imports]
                critical_error = CatalogImportError(
                    id=new_id(),
                    project_id=project_a_id,
                    import_id=critical_import_id,
                    row_number=1,
                    error_code="missing_product_id",
                    error_message="Product identifier is missing",
                    category="completeness-critical",
                )
                session.add_all(
                    (
                        import_record,
                        old_product,
                        stale_product,
                        *unsafe_imports,
                        critical_error,
                    )
                )
                import_id = import_record.id
                await session.commit()

                expired_error = CatalogImportError(
                    id=new_id(),
                    project_id=project_a_id,
                    import_id=critical_import_id,
                    row_number=2,
                    error_code="expired_error",
                    error_message="Expired import error",
                    category="non-critical",
                    created_at=datetime.now(UTC) - timedelta(days=31),
                )
                retained_error = CatalogImportError(
                    id=new_id(),
                    project_id=project_a_id,
                    import_id=critical_import_id,
                    row_number=3,
                    error_code="retained_error",
                    error_message="Retained import error",
                    category="non-critical",
                    created_at=datetime.now(UTC) - timedelta(days=29),
                )
                session.add_all((expired_error, retained_error))
                await session.flush()
                assert await purge_expired_activity(session) == (0, 0, 1)
                remaining_error_ids = set(
                    (
                        await session.scalars(
                            select(CatalogImportError.id).where(
                                CatalogImportError.id.in_((expired_error.id, retained_error.id))
                            )
                        )
                    ).all()
                )
                assert expired_error.id not in remaining_error_ids
                assert retained_error.id in remaining_error_ids
                await session.commit()

                with pytest.raises(IntegrityError):
                    async with session.begin_nested():
                        session.add(
                            CatalogImportError(
                                id=new_id(),
                                project_id=project_a_id,
                                import_id=critical_import_id,
                                row_number=1,
                                error_code="duplicate_row_error",
                                error_message="Duplicate row error",
                                category="non-critical",
                            )
                        )
                        await session.flush()
                await session.commit()

                with pytest.raises(LookupError, match="Project not found"):
                    await finalize_catalog_import(
                        session,
                        principal=Principal(
                            user_id="foreign-owner",
                            account_id=account_b.id,
                            role=AccountRole.OWNER,
                            scopes=frozenset(),
                        ),
                        project_id=project_a_id,
                        import_id="foreign-import-id",
                        products=[],
                    )

                for position, unsafe_import_id in enumerate(unsafe_import_ids):
                    with pytest.raises(CatalogImportFinalizationError):
                        await finalize_catalog_import(
                            session,
                            principal=Principal(
                                user_id="catalog-owner",
                                account_id=account_a_id,
                                role=AccountRole.OWNER,
                                scopes=frozenset(),
                            ),
                            project_id=project_a_id,
                            import_id=unsafe_import_id,
                            products=[
                                {
                                    "external_id": f"unsafe-{position}",
                                    "name": "Must not be applied",
                                    "stock": 1,
                                }
                            ],
                        )

                    failed_import = await session.get(CatalogImport, unsafe_import_id)
                    unchanged_old = await session.scalar(
                        select(Product).where(
                            Product.project_id == project_a_id,
                            Product.external_id == "old",
                        )
                    )
                    absent_unsafe_product = await session.scalar(
                        select(Product).where(
                            Product.project_id == project_a_id,
                            Product.external_id == f"unsafe-{position}",
                        )
                    )
                    assert failed_import is not None
                    assert failed_import.status == "failed"
                    assert unchanged_old is not None
                    assert unchanged_old.status == "active"
                    assert absent_unsafe_product is None
                    await session.commit()

                with pytest.raises(IntegrityError):
                    await finalize_catalog_import(
                        session,
                        principal=Principal(
                            user_id="catalog-owner",
                            account_id=account_a_id,
                            role=AccountRole.OWNER,
                            scopes=frozenset(),
                        ),
                        project_id=project_a_id,
                        import_id=import_id,
                        products=[
                            {"external_id": "new", "name": "New product", "stock": 1},
                            {"external_id": "invalid", "name": "Invalid product", "stock": -1},
                        ],
                    )

                unchanged_old = await session.scalar(
                    select(Product).where(
                        Product.project_id == project_a_id,
                        Product.external_id == "old",
                    )
                )
                absent_new = await session.scalar(
                    select(Product).where(
                        Product.project_id == project_a_id,
                        Product.external_id == "new",
                    )
                )
                unchanged_import = await session.get(CatalogImport, import_id)
                assert unchanged_old is not None and unchanged_old.status == "active"
                assert absent_new is None
                assert unchanged_import is not None and unchanged_import.status == "processing"
                await session.commit()

                await finalize_catalog_import(
                    session,
                    principal=Principal(
                        user_id="catalog-owner",
                        account_id=account_a_id,
                        role=AccountRole.OWNER,
                        scopes=frozenset(),
                    ),
                    project_id=project_a_id,
                    import_id=import_id,
                    products=[
                        {
                            "external_id": "old",
                            "name": "Updated product",
                            "stock": 2,
                        },
                        {"external_id": "new", "name": "New product", "stock": 1},
                    ],
                )
                finalized_old = await session.scalar(
                    select(Product).where(
                        Product.project_id == project_a_id,
                        Product.external_id == "old",
                    )
                )
                finalized_stale = await session.scalar(
                    select(Product).where(
                        Product.project_id == project_a_id,
                        Product.external_id == "stale",
                    )
                )
                finalized_new = await session.scalar(
                    select(Product).where(
                        Product.project_id == project_a_id,
                        Product.external_id == "new",
                    )
                )
                finalized_import = await session.get(CatalogImport, import_id)
                assert finalized_old is not None
                await session.refresh(finalized_old)
                assert finalized_old.status == "active"
                assert finalized_old.name == "Updated product"
                assert finalized_stale is not None
                assert finalized_stale.status == "inactive"
                assert finalized_new is not None
                assert finalized_new.last_seen_import_id == import_id
                assert finalized_import is not None and finalized_import.status == "completed"
        finally:
            async with engine.begin() as connection:
                await connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await engine.dispose()

    asyncio.run(verify())
