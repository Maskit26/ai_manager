from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ErrorDetail(BaseModel):
    field: str
    reason: str


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: list[ErrorDetail] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "error": {
                        "code": "forbidden",
                        "message": "Insufficient permission",
                        "request_id": "f36a24a4-6ce0-4b9b-a5fb-362f248f1013",
                    }
                }
            ]
        }
    )


class AccountRead(BaseModel):
    id: UUID
    status: str

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "e715f8da-7b6f-4de5-8d74-c9b727255456",
                    "status": "active",
                }
            ]
        },
    )


class ProjectListQuery(BaseModel):
    status: str | None = Field(
        default=None,
        description="Optional exact project status filter.",
    )

    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "active"}]})


class ProjectRead(BaseModel):
    id: UUID
    locale: str
    timezone: str
    status: str

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "9e1897bd-6a91-4bc2-97c3-0c0fe3157ad8",
                    "locale": "ru-RU",
                    "timezone": "Europe/Moscow",
                    "status": "active",
                }
            ]
        },
    )


class ProjectListResponse(BaseModel):
    items: list[ProjectRead]

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "items": [
                        {
                            "id": "9e1897bd-6a91-4bc2-97c3-0c0fe3157ad8",
                            "locale": "ru-RU",
                            "timezone": "Europe/Moscow",
                            "status": "active",
                        }
                    ]
                }
            ]
        }
    )


class ProjectCreateRequest(BaseModel):
    locale: str = Field(min_length=1, max_length=32)
    timezone: str = Field(min_length=1, max_length=64)

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"locale": "ru-RU", "timezone": "Europe/Moscow"}]},
    )


class ProjectUpdateRequest(BaseModel):
    locale: str | None = Field(default=None, min_length=1, max_length=32)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def require_update_field(self) -> "ProjectUpdateRequest":
        if self.locale is None and self.timezone is None:
            raise ValueError("At least one project field must be provided")
        return self


class ProjectDeletionRead(BaseModel):
    project_id: UUID
    project_status: Literal["archived"]
    deletion_status: Literal["pending"]
    previous_status: str
    requested_at: datetime


class AccountMemberRead(BaseModel):
    user_id: UUID
    role: Literal["owner", "admin", "operator"]
    status: Literal["active", "disabled"]


class AccountMemberListResponse(BaseModel):
    items: list[AccountMemberRead]


class AccountMemberCreateRequest(BaseModel):
    user_id: UUID
    role: Literal["admin", "operator"]

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "user_id": "dfe2eaf8-797b-4442-9d65-f6468751d9a8",
                    "role": "operator",
                }
            ]
        },
    )


class AccountMemberUpdateRequest(BaseModel):
    role: Literal["admin", "operator"] | None = None
    status: Literal["active", "disabled"] | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def require_one_update_field(self) -> "AccountMemberUpdateRequest":
        if (self.role is None) == (self.status is None):
            raise ValueError("Specify exactly one of role or status")
        return self


class OnboardingAccountRequest(BaseModel):
    identity_provider: str = Field(default="bitrix", min_length=1, max_length=100)
    external_account_id: str = Field(min_length=1)
    external_owner_id: str = Field(min_length=1)

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "identity_provider": "bitrix",
                    "external_account_id": "company-42",
                    "external_owner_id": "user-108",
                }
            ]
        },
    )


class OnboardingAccountRead(BaseModel):
    account_id: UUID
    owner_user_id: UUID
    owner_membership_created: bool


class AgentDraftUpdateRequest(BaseModel):
    config: dict[str, Any]

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "config": {
                        "prompt": "Answer customer questions",
                        "tone": "helpful",
                    }
                }
            ]
        },
    )


class AgentStateRead(BaseModel):
    id: UUID
    project_id: UUID
    status: str
    active_release_id: UUID | None
    draft_config: dict[str, Any]


class AgentReleaseRead(BaseModel):
    id: UUID
    project_id: UUID
    agent_id: UUID
    version: int
    snapshot: dict[str, Any]
    created_at: datetime


class AgentRollbackRequest(BaseModel):
    release_id: UUID

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"release_id": "a8447a6b-1fc9-4e8f-a5d4-2812f3d9ce37"}]},
    )


COMMON_ERROR_RESPONSES: dict[int, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Malformed request."},
    401: {"model": ErrorResponse, "description": "Authentication required."},
    402: {"model": ErrorResponse, "description": "Payment required."},
    403: {"model": ErrorResponse, "description": "Insufficient scope or role."},
    404: {"model": ErrorResponse, "description": "Resource not found."},
    409: {"model": ErrorResponse, "description": "Request conflicts with current state."},
    413: {"model": ErrorResponse, "description": "Request body is too large."},
    415: {"model": ErrorResponse, "description": "Unsupported media type."},
    422: {"model": ErrorResponse, "description": "Request validation failed."},
    429: {"model": ErrorResponse, "description": "Rate limit exceeded."},
    500: {"model": ErrorResponse, "description": "Unexpected server error."},
    503: {"model": ErrorResponse, "description": "Service is not configured."},
}
