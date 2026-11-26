from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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
}
