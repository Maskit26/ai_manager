from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.persistence.authorization_service import require_project_belongs_to_account
from ai_seller_core.persistence.billing_models import (
    AgentSlot,
    DancoinBalance,
    Subscription,
    TariffPlan,
)
from ai_seller_core.persistence.billing_service import (
    _available_balance,
    grant_dancoin,
)
from ai_seller_core.persistence.models import Account, AccountEntitlement, Project

ENTITLEMENT_TTL = timedelta(seconds=60)

_CURRENT_SUBSCRIPTION_STATUSES = ("trial", "active", "grace_period", "parking")
_FEATURE_KEYS = (
    "llm_calls_enabled",
    "catalog_import_enabled",
    "knowledge_base_enabled",
    "multi_channel_enabled",
)
_LIMIT_KEYS = (
    "max_projects",
    "max_catalog_sources_per_project",
    "max_products_per_project",
    "max_kb_documents_per_project",
)
_FEATURE_ACTIONS = {
    "catalog_import": "catalog_import_enabled",
    "knowledge_base": "knowledge_base_enabled",
    "multi_channel": "multi_channel_enabled",
}

EntitlementAction = Literal[
    "llm_call",
    "catalog_import",
    "knowledge_base",
    "multi_channel",
    "project_create",
    "agent_create",
    "configuration",
    "billing",
]


class EntitlementAccessError(ValueError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        super().__init__(message)


class EntitlementConfigurationError(RuntimeError):
    pass


class EntitlementConsistencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class EntitlementSnapshot:
    account_id: str
    subscription_status: str | None
    current_period_end: datetime | None
    grace_until: datetime | None
    features: dict[str, bool]
    limits: dict[str, int | None]
    available_dancoin: Decimal
    included_agents: int
    max_agents: int | None
    expires_at: datetime

    @property
    def effective_agent_limit(self) -> int:
        if self.max_agents is None:
            return self.included_agents
        return min(self.included_agents, self.max_agents)


def _require_transaction(db: AsyncSession) -> None:
    if not db.in_transaction():
        raise RuntimeError("Entitlements operations require an active transaction")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Entitlements timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _datetime_from_snapshot(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise EntitlementConfigurationError("Invalid cached entitlement timestamp")
    try:
        return _utc(datetime.fromisoformat(value))
    except ValueError as exc:
        raise EntitlementConfigurationError("Invalid cached entitlement timestamp") from exc


def _nonnegative_integer(value: Any, *, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EntitlementConfigurationError(f"Tariff value {key!r} must be a nonnegative integer")
    return value


def _plan_features(raw_features: Any) -> dict[str, bool]:
    if raw_features is None:
        raw_features = {}
    if not isinstance(raw_features, dict):
        raise EntitlementConfigurationError("Tariff features must be a JSON object")

    features: dict[str, bool] = {}
    for key in _FEATURE_KEYS:
        value = raw_features.get(key, False)
        if not isinstance(value, bool):
            raise EntitlementConfigurationError(f"Tariff feature {key!r} must be a boolean")
        features[key] = value
    return features


def _plan_limits(raw_limits: Any) -> dict[str, int | None]:
    if raw_limits is None:
        raw_limits = {}
    if not isinstance(raw_limits, dict):
        raise EntitlementConfigurationError("Tariff limits must be a JSON object")

    limits: dict[str, int | None] = {}
    for key in _LIMIT_KEYS:
        value = raw_limits.get(key)
        limits[key] = None if value is None else _nonnegative_integer(value, key=key)
    return limits


def _snapshot_from_row(row: AccountEntitlement) -> EntitlementSnapshot:
    raw = row.snapshot
    if not isinstance(raw, dict):
        raise EntitlementConfigurationError("Cached entitlements must be a JSON object")

    features = raw.get("features")
    limits = raw.get("limits")
    if not isinstance(features, dict) or not isinstance(limits, dict):
        raise EntitlementConfigurationError("Cached entitlement feature/limit data is invalid")

    try:
        available_dancoin = Decimal(str(raw["available_dancoin"]))
        included_agents = _nonnegative_integer(raw["included_agents"], key="included_agents")
        max_agents_value = raw.get("max_agents")
        max_agents = (
            None
            if max_agents_value is None
            else _nonnegative_integer(max_agents_value, key="max_agents")
        )
    except (KeyError, ArithmeticError, ValueError) as exc:
        raise EntitlementConfigurationError("Cached entitlement values are invalid") from exc
    if not available_dancoin.is_finite() or available_dancoin < 0:
        raise EntitlementConfigurationError("Cached available Dancoin is invalid")

    if any(not isinstance(value, bool) for value in features.values()):
        raise EntitlementConfigurationError("Cached entitlement features are invalid")
    for key, value in limits.items():
        if value is not None:
            _nonnegative_integer(value, key=key)

    return EntitlementSnapshot(
        account_id=row.account_id,
        subscription_status=raw.get("subscription_status"),
        current_period_end=_datetime_from_snapshot(raw.get("current_period_end")),
        grace_until=_datetime_from_snapshot(raw.get("grace_until")),
        features=features,
        limits=limits,
        available_dancoin=available_dancoin,
        included_agents=included_agents,
        max_agents=max_agents,
        expires_at=_utc(row.expires_at)
        if row.expires_at is not None
        else datetime.min.replace(tzinfo=UTC),
    )


async def _ensure_entitlement_row(db: AsyncSession, account_id: str) -> AccountEntitlement:
    account_exists = await db.scalar(select(Account.id).where(Account.id == account_id))
    if account_exists is None:
        raise EntitlementAccessError(404, "account_not_found", "Account does not exist")

    await db.execute(
        insert(AccountEntitlement)
        .values(account_id=account_id)
        .on_conflict_do_nothing(index_elements=[AccountEntitlement.account_id])
    )
    row = await db.scalar(
        select(AccountEntitlement)
        .where(AccountEntitlement.account_id == account_id)
        .with_for_update()
    )
    if row is None:
        raise EntitlementConsistencyError("Failed to load account entitlement row")
    return row


async def _sync_project_slot_limits(
    db: AsyncSession,
    *,
    account_id: str,
    slot_limit: int,
    now: datetime,
) -> None:
    project_ids = (
        await db.scalars(select(Project.id).where(Project.account_id == account_id))
    ).all()
    if not project_ids:
        return

    await db.execute(
        insert(AgentSlot)
        .values(
            [
                {
                    "project_id": project_id,
                    "slot_limit": slot_limit,
                    "used_slots": 0,
                    "updated_at": now,
                }
                for project_id in project_ids
            ]
        )
        .on_conflict_do_update(
            index_elements=[AgentSlot.project_id],
            set_={"slot_limit": slot_limit, "updated_at": now},
        )
    )


async def get_entitlements(
    db: AsyncSession,
    *,
    account_id: str,
    now: datetime | None = None,
) -> EntitlementSnapshot:
    _require_transaction(db)
    instant = _utc(now or datetime.now(UTC))
    row = await _ensure_entitlement_row(db, account_id)
    if await _activate_due_subscription_change(db, account_id=account_id, now=instant):
        row.expires_at = instant
    if row.expires_at is not None and _utc(row.expires_at) > instant:
        return _snapshot_from_row(row)

    subscription_and_plan = (
        await db.execute(
            select(Subscription, TariffPlan)
            .join(TariffPlan, TariffPlan.id == Subscription.tariff_plan_id)
            .where(
                Subscription.account_id == account_id,
                Subscription.status.in_(_CURRENT_SUBSCRIPTION_STATUSES),
            )
        )
    ).one_or_none()
    subscription, plan = subscription_and_plan if subscription_and_plan else (None, None)

    balance = await db.get(DancoinBalance, account_id)
    available_dancoin = (
        _available_balance(
            balance.grant_balance,
            balance.purchased_balance,
            balance.reserved_grant,
            balance.reserved_purchased,
            balance.debt_balance,
        )
        if balance is not None
        else Decimal("0.00")
    )
    if not available_dancoin.is_finite() or available_dancoin < 0:
        raise EntitlementConsistencyError("Available Dancoin must be a finite nonnegative amount")

    subscription_snapshot = subscription.snapshot if subscription is not None else {}
    if not isinstance(subscription_snapshot, dict):
        raise EntitlementConfigurationError("Subscription snapshot must be a JSON object")
    features = _plan_features(subscription_snapshot.get("features"))
    limits = _plan_limits(subscription_snapshot.get("limits"))
    included_agents_value = subscription_snapshot.get(
        "included_agents", plan.included_agents if plan is not None else None
    )
    max_agents_value = subscription_snapshot.get(
        "max_agents", plan.max_agents if plan is not None else None
    )
    included_agents = (
        0
        if included_agents_value is None
        else _nonnegative_integer(included_agents_value, key="included_agents")
    )
    max_agents = (
        None
        if max_agents_value is None
        else _nonnegative_integer(max_agents_value, key="max_agents")
    )
    slot_limit = included_agents if max_agents is None else min(included_agents, max_agents)
    await _sync_project_slot_limits(
        db,
        account_id=account_id,
        slot_limit=slot_limit,
        now=instant,
    )

    expires_at = instant + ENTITLEMENT_TTL
    if (
        subscription is not None
        and subscription.pending_snapshot is not None
        and subscription.pending_period_start is not None
    ):
        pending_start = _utc(subscription.pending_period_start)
        if pending_start > instant:
            expires_at = min(expires_at, pending_start)
    snapshot = {
        "subscription_status": subscription.status if subscription is not None else None,
        "current_period_end": (
            _utc(subscription.current_period_end).isoformat()
            if subscription is not None and subscription.current_period_end is not None
            else None
        ),
        "grace_until": (
            _utc(subscription.grace_until).isoformat()
            if subscription is not None and subscription.grace_until is not None
            else None
        ),
        "features": features,
        "limits": limits,
        "available_dancoin": str(available_dancoin),
        "included_agents": included_agents,
        "max_agents": max_agents,
    }

    row.catalog_import_enabled = features["catalog_import_enabled"]
    row.knowledge_base_enabled = features["knowledge_base_enabled"]
    row.multi_channel_enabled = features["multi_channel_enabled"]
    row.max_projects = limits["max_projects"]
    row.max_catalog_sources_per_project = limits["max_catalog_sources_per_project"]
    row.max_products_per_project = limits["max_products_per_project"]
    row.max_kb_documents_per_project = limits["max_kb_documents_per_project"]
    row.snapshot = snapshot
    row.expires_at = expires_at
    await db.flush()
    return _snapshot_from_row(row)


async def _activate_due_subscription_change(
    db: AsyncSession,
    *,
    account_id: str,
    now: datetime,
) -> bool:
    subscription = await db.scalar(
        select(Subscription)
        .where(
            Subscription.account_id == account_id,
            Subscription.pending_snapshot.is_not(None),
            Subscription.pending_period_start <= now,
        )
        .with_for_update()
    )
    if subscription is None:
        return False

    snapshot = subscription.pending_snapshot
    if not isinstance(snapshot, dict):
        raise EntitlementConfigurationError("Pending subscription snapshot must be an object")
    catalog_item = snapshot.get("catalog_item")
    invoice_id = snapshot.get("billing_invoice_id")
    if (
        not isinstance(catalog_item, dict)
        or not isinstance(catalog_item.get("id"), str)
        or not isinstance(invoice_id, str)
    ):
        raise EntitlementConfigurationError("Pending subscription snapshot is incomplete")
    if subscription.pending_tariff_plan_id != catalog_item["id"]:
        raise EntitlementConfigurationError("Pending subscription plan does not match its snapshot")
    if subscription.pending_period_start is None or subscription.pending_period_end is None:
        raise EntitlementConfigurationError("Pending subscription period is incomplete")

    grant_value = snapshot.get("dancoin_grant_amount", "0.00")
    try:
        grant_amount = Decimal(str(grant_value))
    except (InvalidOperation, ValueError) as error:
        raise EntitlementConfigurationError(
            "Pending subscription Dancoin grant is invalid"
        ) from error
    if not grant_amount.is_finite() or grant_amount < 0:
        raise EntitlementConfigurationError("Pending subscription Dancoin grant is invalid")

    subscription.tariff_plan_id = catalog_item["id"]
    subscription.status = "active"
    subscription.current_period_start = _utc(subscription.pending_period_start)
    subscription.current_period_end = _utc(subscription.pending_period_end)
    subscription.grace_until = None
    subscription.canceled_at = None
    subscription.snapshot = snapshot
    subscription.pending_tariff_plan_id = None
    subscription.pending_billing_period = None
    subscription.pending_effective_at = None
    subscription.pending_period_start = None
    subscription.pending_period_end = None
    subscription.pending_snapshot = None
    if grant_amount > 0:
        await grant_dancoin(
            db,
            account_id=account_id,
            reference=f"subscription-grant:{invoice_id}",
            amount=grant_amount,
        )
    await db.flush()
    return True


def _has_active_subscription(snapshot: EntitlementSnapshot, now: datetime) -> bool:
    return snapshot.subscription_status in {"trial", "active"} and (
        snapshot.current_period_end is None or snapshot.current_period_end > now
    )


def _has_configuration_grace(snapshot: EntitlementSnapshot, now: datetime) -> bool:
    return (
        snapshot.subscription_status == "grace_period"
        and snapshot.grace_until is not None
        and snapshot.grace_until > now
    )


async def _reserve_agent_slot(
    db: AsyncSession,
    *,
    account_id: str,
    project_id: str,
    snapshot: EntitlementSnapshot,
    now: datetime,
) -> None:
    project_status = await db.scalar(
        select(Project.status).where(
            Project.id == project_id,
            Project.account_id == account_id,
        )
    )
    if project_status is None:
        raise EntitlementAccessError(404, "project_not_found", "Project does not exist")
    if project_status != "active":
        raise EntitlementAccessError(403, "project_inactive", "Project does not accept new agents")

    await db.execute(
        insert(AgentSlot)
        .values(
            project_id=project_id,
            slot_limit=snapshot.effective_agent_limit,
            used_slots=0,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=[AgentSlot.project_id])
    )
    slot = await db.scalar(
        select(AgentSlot).where(AgentSlot.project_id == project_id).with_for_update()
    )
    if slot is None:
        raise EntitlementConsistencyError("Failed to load project agent slots")

    if slot.slot_limit != snapshot.effective_agent_limit:
        slot.slot_limit = snapshot.effective_agent_limit
        slot.updated_at = now
    if slot.used_slots >= slot.slot_limit:
        raise EntitlementAccessError(403, "agent_limit_exceeded", "No included agent slots remain")

    slot.used_slots += 1
    await db.flush()


async def release_agent_slot(
    db: AsyncSession,
    *,
    project_id: str,
) -> None:
    _require_transaction(db)
    slot = await db.scalar(
        select(AgentSlot).where(AgentSlot.project_id == project_id).with_for_update()
    )
    if slot is None or slot.used_slots <= 0:
        raise EntitlementConsistencyError("Cannot release an unallocated agent slot")
    slot.used_slots -= 1
    await db.flush()


async def check_access(
    db: AsyncSession,
    *,
    account_id: str,
    action: EntitlementAction,
    project_id: str | None = None,
    now: datetime | None = None,
) -> EntitlementSnapshot:
    """Enforce access in the resource transaction; agent_create reserves one slot."""
    _require_transaction(db)
    if project_id is not None:
        await require_project_belongs_to_account(db, account_id=account_id, project_id=project_id)
    instant = _utc(now or datetime.now(UTC))
    snapshot = await get_entitlements(db, account_id=account_id, now=instant)

    if action == "billing":
        return snapshot
    if action == "configuration":
        if _has_active_subscription(snapshot, instant) or _has_configuration_grace(
            snapshot, instant
        ):
            return snapshot
        raise EntitlementAccessError(
            402, "subscription_inactive", "Subscription does not permit configuration access"
        )

    if not _has_active_subscription(snapshot, instant):
        raise EntitlementAccessError(
            402, "subscription_inactive", "An active subscription is required"
        )

    if action == "llm_call":
        if not snapshot.features["llm_calls_enabled"]:
            raise EntitlementAccessError(
                403, "feature_unavailable", "LLM calls are not in this plan"
            )
        if snapshot.available_dancoin <= 0:
            raise EntitlementAccessError(402, "insufficient_dancoin", "No available Dancoin")
        return snapshot

    feature_key = _FEATURE_ACTIONS.get(action)
    if feature_key is not None:
        if not snapshot.features[feature_key]:
            raise EntitlementAccessError(
                403, "feature_unavailable", f"{action} is not in this plan"
            )
        return snapshot

    if action == "project_create":
        project_count = await db.scalar(
            select(func.count()).select_from(Project).where(Project.account_id == account_id)
        )
        project_limit = snapshot.limits["max_projects"]
        if project_limit is not None and project_count >= project_limit:
            raise EntitlementAccessError(403, "project_limit_exceeded", "Project limit reached")
        return snapshot

    if action == "agent_create":
        if project_id is None:
            raise ValueError("project_id is required for agent_create")
        await _reserve_agent_slot(
            db,
            account_id=account_id,
            project_id=project_id,
            snapshot=snapshot,
            now=instant,
        )
        return snapshot

    raise ValueError(f"Unsupported entitlement action: {action}")
