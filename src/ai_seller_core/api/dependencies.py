from typing import Annotated

from fastapi import Depends, Security
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
