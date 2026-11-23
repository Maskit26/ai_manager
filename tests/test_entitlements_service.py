import asyncio
import io
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from ai_seller_core.core.config import Settings
from ai_seller_core.persistence.billing_models import (
    AgentSlot,
    DancoinBalance,
    Subscription,
    TariffPlan,
)
from ai_seller_core.persistence.entitlements_service import (
    EntitlementAccessError,
    check_access,
    get_entitlements,
    release_agent_slot,
)
from ai_seller_core.persistence.models import Account, AccountEntitlement, Project

ROOT = Path(__file__).resolve().parents[1]


def alembic_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"), output_buffer=io.StringIO())
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config


def test_entitlement_access_cache_invalidation_and_slots_postgresql() -> None:
    asyncio.run(_test_entitlement_access_cache_invalidation_and_slots_postgresql())


async def _test_entitlement_access_cache_invalidation_and_slots_postgresql() -> None:
    try:
        database_url = Settings().database_url.get_secret_value()
    except ValidationError:
        pytest.skip("DATABASE_URL is required for entitlement integration tests")

    engine = create_async_engine(database_url, pool_size=4, max_overflow=0)
    schema = f"test_entitlements_{uuid4().hex}"
    schema_created = False
    account_id = str(uuid4())
    project_id = str(uuid4())
    plan_id = str(uuid4())
    subscription_id = str(uuid4())

    async def in_transaction(operation):
        async with AsyncSession(engine, expire_on_commit=False) as db:
            async with db.begin():
                await db.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                return await operation(db)

    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        schema_created = True

        async with engine.begin() as connection:
            await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))

            def upgrade(sync_connection) -> None:
                config = alembic_config()
                config.attributes["connection"] = sync_connection
                command.upgrade(config, "head")

            await connection.run_sync(upgrade)

        now = datetime.now(UTC)

        async def seed(db: AsyncSession) -> None:
            await db.execute(
                insert(Account).values(
                    id=account_id,
                    external_id=f"entitlement-test-{account_id}",
                    status="active",
                )
            )
            await db.execute(
                insert(TariffPlan).values(
                    id=plan_id,
                    code=f"entitlement-test-{plan_id}",
                    version=1,
                    name="Entitlement test",
                    is_active=True,
                    included_agents=1,
                    max_agents=3,
                    features={
                        "llm_calls_enabled": True,
                        "catalog_import_enabled": True,
                        "knowledge_base_enabled": False,
                        "multi_channel_enabled": False,
                    },
                    limits={
                        "max_projects": 2,
                        "max_catalog_sources_per_project": 2,
                        "max_products_per_project": 10,
                        "max_kb_documents_per_project": 0,
                    },
                )
            )
            await db.execute(
                insert(Project).values(
                    id=project_id,
                    account_id=account_id,
                    locale="ru",
                    timezone="UTC",
                    settings={},
                    status="active",
                )
            )
            await db.execute(
                insert(Subscription).values(
                    id=subscription_id,
                    account_id=account_id,
                    tariff_plan_id=plan_id,
                    status="active",
                    current_period_start=now - timedelta(days=1),
                    current_period_end=now + timedelta(days=30),
                    snapshot={
                        "schema_version": 1,
                        "features": {
                            "llm_calls_enabled": True,
                            "catalog_import_enabled": True,
                            "knowledge_base_enabled": False,
                            "multi_channel_enabled": False,
                        },
                        "limits": {
                            "max_projects": 2,
                            "max_catalog_sources_per_project": 2,
                            "max_products_per_project": 10,
                            "max_kb_documents_per_project": 0,
                        },
                        "included_agents": 1,
                        "max_agents": 3,
                    },
                )
            )
            await db.execute(
                insert(DancoinBalance).values(
                    account_id=account_id,
                    grant_balance=Decimal("3.00"),
                )
            )

        await in_transaction(seed)

        snapshot_now = datetime.now(UTC)
        snapshot = await in_transaction(
            lambda db: get_entitlements(db, account_id=account_id, now=snapshot_now)
        )
        assert snapshot.subscription_status == "active"
        assert snapshot.features["llm_calls_enabled"] is True
        assert snapshot.limits["max_projects"] == 2
        assert snapshot.limits["max_catalog_sources_per_project"] == 2
        assert snapshot.available_dancoin == Decimal("3.00")
        assert snapshot.effective_agent_limit == 1
        assert snapshot.expires_at == snapshot_now + timedelta(seconds=60)

        await in_transaction(lambda db: check_access(db, account_id=account_id, action="llm_call"))
        await in_transaction(
            lambda db: check_access(db, account_id=account_id, action="catalog_import")
        )

        with pytest.raises(EntitlementAccessError) as feature_error:
            await in_transaction(
                lambda db: check_access(db, account_id=account_id, action="knowledge_base")
            )
        assert feature_error.value.status_code == 403
        assert feature_error.value.code == "feature_unavailable"

        async def create_project_candidate(candidate_id: str) -> Exception | None:
            async def create(db: AsyncSession) -> None:
                await check_access(db, account_id=account_id, action="project_create")
                await db.execute(
                    insert(Project).values(
                        id=candidate_id,
                        account_id=account_id,
                        locale="ru",
                        timezone="UTC",
                        settings={},
                        status="active",
                    )
                )

            try:
                await in_transaction(create)
            except EntitlementAccessError as exc:
                return exc
            return None

        project_creation_results = await asyncio.gather(
            create_project_candidate(str(uuid4())),
            create_project_candidate(str(uuid4())),
        )
        assert sum(result is None for result in project_creation_results) == 1
        denied_project_results = [
            result
            for result in project_creation_results
            if isinstance(result, EntitlementAccessError)
        ]
        assert len(denied_project_results) == 1
        assert denied_project_results[0].code == "project_limit_exceeded"
        with pytest.raises(EntitlementAccessError) as project_error:
            await in_transaction(
                lambda db: check_access(db, account_id=account_id, action="project_create")
            )
        assert project_error.value.status_code == 403
        assert project_error.value.code == "project_limit_exceeded"

        async def create_agent() -> object:
            return await in_transaction(
                lambda db: check_access(
                    db,
                    account_id=account_id,
                    action="agent_create",
                    project_id=project_id,
                )
            )

        concurrent_agent_results = await asyncio.gather(
            create_agent(),
            create_agent(),
            return_exceptions=True,
        )
        assert (
            sum(not isinstance(result, BaseException) for result in concurrent_agent_results) == 1
        )
        denied_agent_results = [
            result
            for result in concurrent_agent_results
            if isinstance(result, EntitlementAccessError)
        ]
        assert len(denied_agent_results) == 1
        assert denied_agent_results[0].code == "agent_limit_exceeded"
        slot = await in_transaction(
            lambda db: db.scalar(select(AgentSlot).where(AgentSlot.project_id == project_id))
        )
        assert slot is not None
        assert slot.slot_limit == 1
        assert slot.used_slots == 1
        cached_entitlement = await in_transaction(
            lambda db: db.scalar(
                select(AccountEntitlement).where(AccountEntitlement.account_id == account_id)
            )
        )
        assert cached_entitlement.expires_at <= datetime.now(UTC)

        await in_transaction(lambda db: release_agent_slot(db, project_id=project_id))
        slot = await in_transaction(
            lambda db: db.scalar(select(AgentSlot).where(AgentSlot.project_id == project_id))
        )
        assert slot.used_slots == 0
        await in_transaction(
            lambda db: db.execute(
                update(Project).where(Project.id == project_id).values(status="archived")
            )
        )
        with pytest.raises(EntitlementAccessError) as inactive_project_error:
            await in_transaction(
                lambda db: check_access(
                    db,
                    account_id=account_id,
                    action="agent_create",
                    project_id=project_id,
                )
            )
        assert inactive_project_error.value.code == "project_inactive"

        await in_transaction(
            lambda db: db.execute(
                update(DancoinBalance)
                .where(DancoinBalance.account_id == account_id)
                .values(grant_balance=Decimal("0.00"))
            )
        )
        with pytest.raises(EntitlementAccessError) as balance_error:
            await in_transaction(
                lambda db: check_access(db, account_id=account_id, action="llm_call")
            )
        assert balance_error.value.status_code == 402
        assert balance_error.value.code == "insufficient_dancoin"

        await in_transaction(
            lambda db: db.execute(
                update(DancoinBalance)
                .where(DancoinBalance.account_id == account_id)
                .values(purchased_balance=Decimal("3.00"), debt_balance=Decimal("1.00"))
            )
        )
        with pytest.raises(EntitlementAccessError) as debt_error:
            await in_transaction(
                lambda db: check_access(db, account_id=account_id, action="llm_call")
            )
        assert debt_error.value.code == "insufficient_dancoin"

        await in_transaction(
            lambda db: db.execute(
                update(DancoinBalance)
                .where(DancoinBalance.account_id == account_id)
                .values(purchased_balance=Decimal("3.00"), debt_balance=Decimal("0.00"))
            )
        )
        await in_transaction(
            lambda db: db.execute(
                update(Subscription)
                .where(Subscription.id == subscription_id)
                .values(status="grace_period", grace_until=now + timedelta(days=6))
            )
        )
        with pytest.raises(EntitlementAccessError) as grace_llm_error:
            await in_transaction(
                lambda db: check_access(db, account_id=account_id, action="llm_call")
            )
        assert grace_llm_error.value.code == "subscription_inactive"
        await in_transaction(
            lambda db: check_access(db, account_id=account_id, action="configuration")
        )
        await in_transaction(lambda db: check_access(db, account_id=account_id, action="billing"))

        await in_transaction(
            lambda db: db.execute(
                update(Subscription)
                .where(Subscription.id == subscription_id)
                .values(status="parking", grace_until=now - timedelta(seconds=1))
            )
        )
        with pytest.raises(EntitlementAccessError) as parking_error:
            await in_transaction(
                lambda db: check_access(db, account_id=account_id, action="configuration")
            )
        assert parking_error.value.status_code == 402
        await in_transaction(lambda db: check_access(db, account_id=account_id, action="billing"))

        await in_transaction(
            lambda db: db.execute(
                update(Subscription)
                .where(Subscription.id == subscription_id)
                .values(status="canceled")
            )
        )
        with pytest.raises(EntitlementAccessError) as canceled_error:
            await in_transaction(
                lambda db: check_access(db, account_id=account_id, action="llm_call")
            )
        assert canceled_error.value.code == "subscription_inactive"
        await in_transaction(lambda db: check_access(db, account_id=account_id, action="billing"))

        await in_transaction(
            lambda db: db.execute(
                update(Subscription)
                .where(Subscription.id == subscription_id)
                .values(status="trial", grace_until=None)
            )
        )
        trial_snapshot = await in_transaction(
            lambda db: check_access(db, account_id=account_id, action="llm_call")
        )
        assert trial_snapshot.subscription_status == "trial"

        await in_transaction(
            lambda db: db.execute(
                update(TariffPlan)
                .where(TariffPlan.id == plan_id)
                .values(features={"llm_calls_enabled": False})
            )
        )
        snapshot_after_catalog_edit = await in_transaction(
            lambda db: check_access(db, account_id=account_id, action="llm_call")
        )
        assert snapshot_after_catalog_edit.features["llm_calls_enabled"] is True

        await in_transaction(
            lambda db: db.execute(
                update(AgentSlot).where(AgentSlot.project_id == project_id).values(slot_limit=9)
            )
        )
        refreshed_snapshot = await in_transaction(
            lambda db: get_entitlements(db, account_id=account_id)
        )
        assert refreshed_snapshot.features["llm_calls_enabled"] is True
        assert refreshed_snapshot.features["knowledge_base_enabled"] is False
        slot = await in_transaction(
            lambda db: db.scalar(select(AgentSlot).where(AgentSlot.project_id == project_id))
        )
        assert slot.slot_limit == 1
        assert slot.used_slots == 0

        ttl_now = datetime.now(UTC)
        await in_transaction(
            lambda db: db.execute(
                update(AccountEntitlement)
                .where(AccountEntitlement.account_id == account_id)
                .values(expires_at=ttl_now - timedelta(seconds=1))
            )
        )
        ttl_snapshot = await in_transaction(
            lambda db: get_entitlements(db, account_id=account_id, now=ttl_now)
        )
        assert ttl_snapshot.expires_at == ttl_now + timedelta(seconds=60)

        await in_transaction(
            lambda db: db.execute(
                update(Subscription)
                .where(Subscription.id == subscription_id)
                .values(status="active", current_period_end=now - timedelta(seconds=1))
            )
        )
        with pytest.raises(EntitlementAccessError) as expired_error:
            await in_transaction(
                lambda db: check_access(db, account_id=account_id, action="llm_call")
            )
        assert expired_error.value.code == "subscription_inactive"
    finally:
        try:
            if schema_created:
                async with engine.begin() as connection:
                    await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        finally:
            await engine.dispose()
