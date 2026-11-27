from typing import Annotated

from fastapi import Depends, Header, HTTPException, Security, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.api.auth import get_current_principal
from ai_seller_core.persistence.access_policy import Principal
from ai_seller_core.persistence.base import get_session

DatabaseSession = Annotated[AsyncSession, Depends(get_session)]
AccountReader = Annotated[
    Principal,
    Security(get_current_principal, scopes=["account:read"]),
]
ProjectReader = Annotated[
    Principal,
    Security(get_current_principal, scopes=["projects:read"]),
]
ProjectManager = Annotated[
    Principal,
    Security(get_current_principal, scopes=["projects:write"]),
]
AccountMemberReader = Annotated[
    Principal,
    Security(get_current_principal, scopes=["members:read"]),
]
AccountMemberWriter = Annotated[
    Principal,
    Security(get_current_principal, scopes=["members:write"]),
]
AgentReader = Annotated[
    Principal,
    Security(get_current_principal, scopes=["agents:read"]),
]
AgentWriter = Annotated[
    Principal,
    Security(get_current_principal, scopes=["agents:write"]),
]


def require_idempotency_key(
    value: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ],
) -> str:
    if not value.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Idempotency-Key must not be empty",
        )
    return value


IdempotencyKey = Annotated[str, Depends(require_idempotency_key)]
