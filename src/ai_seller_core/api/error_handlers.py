import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ai_seller_core.api.auth import (
    ActiveMembershipRequired,
    InvalidAccessToken,
    UnknownIdentityError,
)
from ai_seller_core.persistence.access_policy import AuthorizationError
from ai_seller_core.persistence.identity_service import IdentityConflictError
from ai_seller_core.persistence.membership_service import MembershipError

logger = logging.getLogger(__name__)

_HTTP_ERROR_CODES = {
    400: "bad_request",
    401: "unauthorized",
    402: "payment_required",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
}

_HTTP_ERROR_MESSAGES = {
    400: "Bad request",
    401: "Authentication required",
    402: "Payment required",
    403: "Insufficient permission",
    404: "Resource not found",
    409: "Request conflicts with current state",
    413: "Request body is too large",
    415: "Unsupported media type",
    422: "Request validation failed",
    429: "Too many requests",
}


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, str]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or str(uuid4())
    error_body: dict[str, object] = {
        "code": code,
        "message": message,
        "request_id": request_id,
    }
    if details:
        error_body["details"] = details
    return JSONResponse(
        status_code=status_code,
        content={"error": error_body},
        headers=headers,
    )


async def handle_http_error(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    status_code = exc.status_code
    return _error_response(
        request,
        status_code=status_code,
        code=_HTTP_ERROR_CODES.get(status_code, "http_error"),
        message=(
            "Internal server error"
            if status_code >= 500
            else _HTTP_ERROR_MESSAGES.get(status_code, "Request failed")
        ),
        headers=exc.headers,
    )


async def handle_request_validation_error(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    details = []
    for error in exc.errors():
        location = error.get("loc", ())
        field = ".".join(str(part) for part in location if part not in {"body", "query", "path"})
        details.append(
            {
                "field": field or "request",
                "reason": str(error.get("type", "invalid_value")),
            }
        )
    return _error_response(
        request,
        status_code=422,
        code="validation_error",
        message="Request validation failed",
        details=details,
    )


async def handle_unauthorized_error(request: Request, _exc: Exception) -> JSONResponse:
    return _error_response(
        request,
        status_code=401,
        code="unauthorized",
        message="Authentication required",
    )


async def handle_forbidden_error(request: Request, _exc: Exception) -> JSONResponse:
    return _error_response(
        request,
        status_code=403,
        code="forbidden",
        message="Insufficient permission",
    )


async def handle_not_found_error(request: Request, _exc: Exception) -> JSONResponse:
    return _error_response(
        request,
        status_code=404,
        code="not_found",
        message="Resource not found",
    )


async def handle_conflict_error(request: Request, _exc: Exception) -> JSONResponse:
    return _error_response(
        request,
        status_code=409,
        code="conflict",
        message="Request conflicts with current state",
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, handle_http_error)
    app.add_exception_handler(RequestValidationError, handle_request_validation_error)
    app.add_exception_handler(InvalidAccessToken, handle_unauthorized_error)
    app.add_exception_handler(UnknownIdentityError, handle_unauthorized_error)
    app.add_exception_handler(ActiveMembershipRequired, handle_forbidden_error)
    app.add_exception_handler(AuthorizationError, handle_forbidden_error)
    app.add_exception_handler(LookupError, handle_not_found_error)
    app.add_exception_handler(IdentityConflictError, handle_conflict_error)
    app.add_exception_handler(MembershipError, handle_conflict_error)
    app.add_exception_handler(Exception, handle_unexpected_error)


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled API exception",
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return _error_response(
        request,
        status_code=500,
        code="internal_error",
        message="Internal server error",
    )
