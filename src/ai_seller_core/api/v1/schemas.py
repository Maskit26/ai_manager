from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class DialogIngressRequest(BaseModel):
    channel_id: str = Field(min_length=1, max_length=100)
    customer_id: str = Field(min_length=1, max_length=100)
    external_chat_id: str = Field(min_length=1, max_length=200)
    external_message_id: str | None = Field(default=None, max_length=200)
    text: str = Field(min_length=1, max_length=20_000)

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "channel_id": "max-channel-1",
                    "customer_id": "customer-42",
                    "external_chat_id": "chat-42",
                    "external_message_id": "message-9001",
                    "text": "Hello, I need help with an order.",
                }
            ]
        },
    )


class DialogTextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"text": "Please check this order."}]},
    )


class DialogRead(BaseModel):
    id: str
    project_id: str
    channel_id: str
    customer_id: str
    external_chat_id: str
    release_id: str
    state: Literal["active", "closed"]
    last_activity_at: datetime
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class DialogListResponse(BaseModel):
    items: list[DialogRead]
    next_cursor: str | None

    model_config = ConfigDict(extra="forbid")


class DialogMessageRead(BaseModel):
    id: str
    session_id: str
    role: Literal["user", "assistant", "operator", "system"]
    content: dict[str, Any]
    delivery_status: Literal[
        "received",
        "pending",
        "sending",
        "sent",
        "retrying",
        "delivery_unknown",
        "failed",
        "not_required",
    ]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class DialogMessagePageResponse(BaseModel):
    items: list[DialogMessageRead]
    next_cursor: str | None

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"items": [], "next_cursor": None}],
        },
    )


class DialogIngressAccepted(BaseModel):
    status: Literal["accepted"]
    event_id: str | None
    duplicate: bool

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "status": "accepted",
                    "event_id": "e7df7007-05eb-437b-98b2-3519d2c0146a",
                    "duplicate": False,
                }
            ]
        },
    )


class DialogWriteAccepted(BaseModel):
    status: Literal["accepted"]
    job_id: str

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "status": "accepted",
                    "job_id": "88e9c2ca-993b-4ce8-8655-323c7ae8f86c",
                }
            ]
        },
    )


class BillingPeriodRead(BaseModel):
    code: str
    months: int
    amount: Decimal
    currency: str
    dancoin_grant_amount: Decimal


class BillingPlanRead(BaseModel):
    id: UUID
    code: str
    version: int
    name: str
    included_agents: int | None
    max_agents: int | None
    features: dict[str, bool]
    limits: dict[str, int | None]
    billing_periods: list[BillingPeriodRead]


class DancoinPackageRead(BaseModel):
    id: UUID
    code: str
    version: int
    name: str
    dancoin_amount: Decimal
    price_amount: Decimal
    currency: str


class BillingPlansResponse(BaseModel):
    plans: list[BillingPlanRead]
    dancoin_packages: list[DancoinPackageRead]

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "plans": [
                        {
                            "id": "9e1897bd-6a91-4bc2-97c3-0c0fe3157ad8",
                            "code": "standard",
                            "version": 1,
                            "name": "Standard",
                            "included_agents": 2,
                            "max_agents": 5,
                            "features": {"llm_calls_enabled": True},
                            "limits": {"max_projects": 3},
                            "billing_periods": [
                                {
                                    "code": "monthly",
                                    "months": 1,
                                    "amount": "12.00",
                                    "currency": "USD",
                                    "dancoin_grant_amount": "10.00",
                                }
                            ],
                        }
                    ],
                    "dancoin_packages": [],
                }
            ]
        }
    )


class BillingPendingChangeRead(BaseModel):
    tariff_plan_id: UUID
    billing_period_code: str
    effective_at: datetime
    status: Literal["awaiting_payment", "paid"]


class BillingSubscriptionRead(BaseModel):
    id: UUID
    tariff_plan_id: UUID
    tariff_plan_code: str
    tariff_plan_name: str
    status: str
    current_period_start: datetime
    current_period_end: datetime | None
    grace_until: datetime | None
    pending_change: BillingPendingChangeRead | None


class BillingEntitlementsRead(BaseModel):
    subscription_status: str | None
    current_period_end: datetime | None
    grace_until: datetime | None
    features: dict[str, bool]
    limits: dict[str, int | None]
    available_dancoin: Decimal
    included_agents: int
    max_agents: int | None


class BillingSubscriptionResponse(BaseModel):
    subscription: BillingSubscriptionRead | None
    entitlements: BillingEntitlementsRead

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "subscription": {
                        "id": "6f3a5d9c-91b5-48a5-baaa-1569ffcc4188",
                        "tariff_plan_id": "9e1897bd-6a91-4bc2-97c3-0c0fe3157ad8",
                        "tariff_plan_code": "standard",
                        "tariff_plan_name": "Standard",
                        "status": "active",
                        "current_period_start": "2026-01-01T00:00:00Z",
                        "current_period_end": "2026-02-01T00:00:00Z",
                        "grace_until": None,
                        "pending_change": None,
                    },
                    "entitlements": {
                        "subscription_status": "active",
                        "current_period_end": "2026-02-01T00:00:00Z",
                        "grace_until": None,
                        "features": {"llm_calls_enabled": True},
                        "limits": {"max_projects": 3},
                        "available_dancoin": "10.00",
                        "included_agents": 2,
                        "max_agents": 5,
                    },
                }
            ]
        }
    )


class DancoinBalanceRead(BaseModel):
    grant_balance: Decimal
    purchased_balance: Decimal
    reserved_grant: Decimal
    reserved_purchased: Decimal
    debt_balance: Decimal
    available: Decimal


class DancoinTransactionRead(BaseModel):
    id: UUID
    type: str
    source: str | None
    amount: Decimal
    balance_after: Decimal
    occurred_at: datetime


class DancoinResponse(BaseModel):
    balance: DancoinBalanceRead
    transactions: list[DancoinTransactionRead]
    next_cursor: str | None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "balance": {
                        "grant_balance": "10.00",
                        "purchased_balance": "5.00",
                        "reserved_grant": "0.00",
                        "reserved_purchased": "0.00",
                        "debt_balance": "0.00",
                        "available": "15.00",
                    },
                    "transactions": [],
                    "next_cursor": None,
                }
            ]
        }
    )


class SubscriptionChangeRequest(BaseModel):
    tariff_plan_id: UUID
    billing_period_code: str = Field(min_length=1, max_length=100)

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "tariff_plan_id": "9e1897bd-6a91-4bc2-97c3-0c0fe3157ad8",
                    "billing_period_code": "annual",
                }
            ]
        },
    )


class SubscriptionChangeResponse(BaseModel):
    status: Literal["scheduled"]
    tariff_plan_id: UUID
    billing_period_code: str
    effective_at: datetime

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "status": "scheduled",
                    "tariff_plan_id": "9e1897bd-6a91-4bc2-97c3-0c0fe3157ad8",
                    "billing_period_code": "annual",
                    "effective_at": "2026-02-01T00:00:00Z",
                }
            ]
        }
    )


class CheckoutRequest(BaseModel):
    tariff_plan_id: UUID
    billing_period_code: str = Field(min_length=1, max_length=100)

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "tariff_plan_id": "9e1897bd-6a91-4bc2-97c3-0c0fe3157ad8",
                    "billing_period_code": "monthly",
                }
            ]
        },
    )


class DancoinPurchaseRequest(BaseModel):
    package_id: UUID

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"package_id": "e715f8da-7b6f-4de5-8d74-c9b727255456"}]},
    )


class CheckoutResponse(BaseModel):
    invoice_id: UUID
    invoice_number: str
    payment_id: UUID
    provider: str
    payment_status: str
    amount: Decimal
    currency: str

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "invoice_id": "6f3a5d9c-91b5-48a5-baaa-1569ffcc4188",
                    "invoice_number": "INV-4F6F5E9A",
                    "payment_id": "88e9c2ca-993b-4ce8-8655-323c7ae8f86c",
                    "provider": "fake",
                    "payment_status": "pending",
                    "amount": "12.00",
                    "currency": "USD",
                }
            ]
        }
    )


class RefundRequestBody(BaseModel):
    payment_id: UUID
    reason: str = Field(min_length=1, max_length=1000)

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "payment_id": "e715f8da-7b6f-4de5-8d74-c9b727255456",
                    "reason": "Payment was duplicated.",
                }
            ]
        },
    )

    @field_validator("reason")
    @classmethod
    def require_nonblank_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Refund reason must not be blank")
        return normalized


class RefundRequestRead(BaseModel):
    id: UUID
    payment_id: UUID
    status: Literal["pending"]
    created_at: datetime

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "6f3a5d9c-91b5-48a5-baaa-1569ffcc4188",
                    "payment_id": "88e9c2ca-993b-4ce8-8655-323c7ae8f86c",
                    "status": "pending",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ]
        }
    )


class InvoiceRead(BaseModel):
    id: UUID
    number: str
    purchase_type: str
    status: str
    total: Decimal
    currency: str
    period_start: datetime | None
    period_end: datetime | None
    created_at: datetime


class InvoicePageResponse(BaseModel):
    items: list[InvoiceRead]
    next_cursor: str | None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "items": [],
                    "next_cursor": None,
                }
            ]
        }
    )


class UsageRecordRead(BaseModel):
    id: UUID
    project_id: UUID | None
    metric: str
    quantity: Decimal
    unit: str
    input_tokens: int | None
    output_tokens: int | None
    model_id: str | None
    price_version: int | None
    dancoin_cost: Decimal
    period_start: datetime
    period_end: datetime | None
    occurred_at: datetime


class UsagePageResponse(BaseModel):
    items: list[UsageRecordRead]
    next_cursor: str | None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "items": [],
                    "next_cursor": None,
                }
            ]
        }
    )


class BillingModelRead(BaseModel):
    model_id: str
    version: int
    name: str
    input_price_per_1k: Decimal
    output_price_per_1k: Decimal
    long_context_threshold_tokens: int | None
    long_context_input_price_per_1k: Decimal | None
    long_context_output_price_per_1k: Decimal | None
    capabilities: dict[str, Any]


class BillingModelsResponse(BaseModel):
    items: list[BillingModelRead]

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "items": [
                        {
                            "model_id": "example-model",
                            "version": 1,
                            "name": "Example model",
                            "input_price_per_1k": "0.00100000",
                            "output_price_per_1k": "0.00200000",
                            "long_context_threshold_tokens": None,
                            "long_context_input_price_per_1k": None,
                            "long_context_output_price_per_1k": None,
                            "capabilities": {},
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
    503: {"model": ErrorResponse, "description": "Service is not configured."},
}
