from copy import deepcopy
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.persistence.models import Agent, AgentDraft, AgentRelease

RELEASE_CONFIG_KEYS = frozenset(
    {
        "prompt",
        "tone",
        "model_settings",
        "sales_rules",
        "functions",
        "response_settings",
    }
)
EXCLUDED_CONFIG_KEYS = frozenset(
    {
        "api_key",
        "access_token",
        "catalog",
        "catalog_id",
        "channel_credentials",
        "client_secret",
        "credential",
        "credentials",
        "api_credentials",
        "authorization",
        "kb",
        "kb_id",
        "knowledge_base",
        "knowledge_base_id",
        "knowledgebase",
        "password",
        "private_key",
        "product_catalog",
        "refresh_token",
        "secret",
        "secrets",
        "token",
        "oauth_token",
    }
)


def _require_transaction(db: AsyncSession) -> None:
    if not db.in_transaction():
        raise RuntimeError("Agent release operations require an active transaction")


def _validate_config(config: dict[str, Any], *, published: bool = False) -> None:
    if not isinstance(config, dict):
        raise ValueError("Agent configuration must be a JSON object")
    unknown = config.keys() - RELEASE_CONFIG_KEYS
    if unknown:
        raise ValueError(f"Unsupported agent configuration fields: {sorted(unknown)}")
    _reject_excluded_fields(config)
    missing = RELEASE_CONFIG_KEYS - config.keys()
    if published and missing:
        raise ValueError(f"Agent release is missing configuration fields: {sorted(missing)}")


def _reject_excluded_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError("Agent configuration object keys must be strings")
            normalized_key = key.casefold().replace("-", "_")
            if (
                normalized_key in EXCLUDED_CONFIG_KEYS
                or normalized_key.endswith("_credentials")
                or normalized_key.endswith(("_secret", "_token"))
                or normalized_key.startswith("catalog_")
                or normalized_key.startswith("knowledge_base")
            ):
                raise ValueError(f"Agent configuration cannot contain excluded field {key!r}")
            _reject_excluded_fields(nested_value)
    elif isinstance(value, list):
        for item in value:
            _reject_excluded_fields(item)


async def create_agent(db: AsyncSession, *, project_id: str) -> Agent:
    _require_transaction(db)
    agent = Agent(project_id=project_id, status="draft")
    db.add(agent)
    await db.flush()
    db.add(AgentDraft(project_id=project_id, agent_id=agent.id, config={}))
    await db.flush()
    return agent


async def save_agent_draft(
    db: AsyncSession,
    *,
    project_id: str,
    config: dict[str, Any],
) -> AgentDraft:
    _require_transaction(db)
    _validate_config(config)
    agent = await db.scalar(select(Agent).where(Agent.project_id == project_id).with_for_update())
    if agent is None:
        raise LookupError(f"No agent exists for project {project_id!r}")

    draft = await db.scalar(
        select(AgentDraft)
        .where(
            AgentDraft.project_id == project_id,
            AgentDraft.agent_id == agent.id,
        )
        .with_for_update()
    )
    if draft is None:
        draft = AgentDraft(project_id=project_id, agent_id=agent.id, config=deepcopy(config))
        db.add(draft)
    else:
        draft.config = deepcopy(config)
    await db.flush()
    return draft


async def publish_agent_release(db: AsyncSession, *, project_id: str) -> AgentRelease:
    _require_transaction(db)
    agent = await db.scalar(select(Agent).where(Agent.project_id == project_id).with_for_update())
    if agent is None:
        raise LookupError(f"No agent exists for project {project_id!r}")

    draft = await db.scalar(
        select(AgentDraft)
        .where(
            AgentDraft.project_id == project_id,
            AgentDraft.agent_id == agent.id,
        )
        .with_for_update()
    )
    if draft is None:
        raise LookupError(f"No draft exists for agent {agent.id!r}")
    _validate_config(draft.config, published=True)

    latest_version = await db.scalar(
        select(func.max(AgentRelease.version)).where(AgentRelease.agent_id == agent.id)
    )
    release = AgentRelease(
        project_id=project_id,
        agent_id=agent.id,
        version=(latest_version or 0) + 1,
        snapshot=deepcopy(draft.config),
    )
    db.add(release)
    await db.flush()

    agent.active_release_id = release.id
    if agent.status == "draft":
        agent.status = "active"
    await db.flush()
    return release


async def rollback_agent_release(
    db: AsyncSession,
    *,
    project_id: str,
    release_id: str,
) -> None:
    _require_transaction(db)
    agent = await db.scalar(select(Agent).where(Agent.project_id == project_id).with_for_update())
    if agent is None:
        raise LookupError(f"No agent exists for project {project_id!r}")

    release = await db.scalar(
        select(AgentRelease).where(
            AgentRelease.project_id == project_id,
            AgentRelease.agent_id == agent.id,
            AgentRelease.id == release_id,
        )
    )
    if release is None:
        raise LookupError(f"Release {release_id!r} does not belong to project {project_id!r}")

    agent.active_release_id = release.id
    await db.flush()
