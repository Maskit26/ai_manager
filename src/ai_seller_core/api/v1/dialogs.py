import base64
import binascii
import hashlib
import json
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import and_, or_, select

from ai_seller_core.api.dependencies import (
    DatabaseSession,
    DialogReader,
    DialogWriter,
    IdempotencyKey,
)
from ai_seller_core.api.v1.schemas import (
    COMMON_ERROR_RESPONSES,
    DialogIngressAccepted,
    DialogIngressRequest,
    DialogListResponse,
    DialogMessagePageResponse,
    DialogRead,
    DialogTextRequest,
    DialogWriteAccepted,
)
from ai_seller_core.jobs.inbox import ingest_channel_event
from ai_seller_core.jobs.repository import enqueue_job
from ai_seller_core.persistence.access_policy import (
    AccountRole,
    AuthorizationError,
    ProjectPermission,
    can_access_project,
    require_scopes,
)
from ai_seller_core.persistence.authorization_service import (
    require_dialog_access,
    require_project_access,
    require_project_belongs_to_account,
)
from ai_seller_core.persistence.idempotency_service import execute_idempotently
from ai_seller_core.persistence.models import (
    AccountMembership,
    Channel,
    ChatMessage,
    DialogOperatorAssignment,
    DialogSession,
    ProjectOperator,
)

router = APIRouter(tags=["dialogs"], responses=COMMON_ERROR_RESPONSES)


def _encode_cursor(timestamp: datetime, resource_id: str) -> str:
    value = json.dumps(
        {"timestamp": timestamp.isoformat(), "id": resource_id},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        encoded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
        if not isinstance(value, dict) or set(value) != {"timestamp", "id"}:
            raise ValueError("Invalid cursor fields")
        timestamp = datetime.fromisoformat(value["timestamp"])
        resource_id = value["id"]
        if timestamp.tzinfo is None or not isinstance(resource_id, str) or not resource_id:
            raise ValueError("Invalid cursor values")
        return timestamp, resource_id
    except (
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid pagination cursor",
        ) from error


async def _get_dialog(
    session: DatabaseSession,
    *,
    principal: DialogReader | DialogWriter,
    dialog_id: str,
    required_scope: str,
) -> DialogSession:
    project_id = await session.scalar(
        select(DialogSession.project_id).where(DialogSession.id == dialog_id)
    )
    if project_id is None:
        raise LookupError(f"Dialog {dialog_id!r} does not exist")
    return await require_dialog_access(
        session,
        principal,
        project_id=project_id,
        session_id=dialog_id,
        required_scopes=(required_scope,),
    )


def _operator_dialog_ids(project_id: str, principal: DialogReader):
    return (
        select(DialogOperatorAssignment.session_id)
        .join(
            AccountMembership,
            and_(
                AccountMembership.id == DialogOperatorAssignment.membership_id,
                AccountMembership.account_id == DialogOperatorAssignment.account_id,
            ),
        )
        .join(
            ProjectOperator,
            and_(
                ProjectOperator.membership_id == AccountMembership.id,
                ProjectOperator.project_id == DialogOperatorAssignment.project_id,
                ProjectOperator.account_id == DialogOperatorAssignment.account_id,
            ),
        )
        .where(
            DialogOperatorAssignment.project_id == project_id,
            DialogOperatorAssignment.account_id == principal.account_id,
            AccountMembership.user_id == principal.user_id,
            AccountMembership.role == AccountRole.OPERATOR.value,
            AccountMembership.status == "active",
            ProjectOperator.status == "active",
        )
    )


@router.get(
    "/projects/{project_id}/dialogs",
    response_model=DialogListResponse,
    responses=COMMON_ERROR_RESPONSES,
    description=(
        "Возвращает список диалогов проекта с фильтрацией по состоянию. "
        "Поддерживает курсорную пагинацию и учитывает права доступа пользователя."
    ),
)
async def list_project_dialogs(
    project_id: str,
    session: DatabaseSession,
    principal: DialogReader,
    state_filter: Annotated[Literal["active", "closed"] | None, Query(alias="state")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
) -> DialogListResponse:
    require_scopes(principal, ("dialogs:read",))
    await require_project_belongs_to_account(
        session,
        account_id=principal.account_id,
        project_id=project_id,
    )

    statement = select(DialogSession).where(DialogSession.project_id == project_id)
    if state_filter is not None:
        statement = statement.where(DialogSession.state == state_filter)
    if can_access_project(principal.role, ProjectPermission.READ):
        pass
    elif principal.role == AccountRole.OPERATOR:
        statement = statement.where(
            DialogSession.id.in_(_operator_dialog_ids(project_id, principal))
        )
    else:
        raise AuthorizationError("Principal is not authorized to list project dialogs")

    if cursor is not None:
        cursor_created_at, cursor_id = _decode_cursor(cursor)
        statement = statement.where(
            or_(
                DialogSession.last_activity_at < cursor_created_at,
                and_(
                    DialogSession.last_activity_at == cursor_created_at,
                    DialogSession.id < cursor_id,
                ),
            )
        )
    dialogs = (
        await session.scalars(
            statement.order_by(
                DialogSession.last_activity_at.desc(),
                DialogSession.id.desc(),
            ).limit(limit + 1)
        )
    ).all()
    has_more = len(dialogs) > limit
    page = dialogs[:limit]
    next_cursor = _encode_cursor(page[-1].last_activity_at, page[-1].id) if has_more else None
    return DialogListResponse(items=page, next_cursor=next_cursor)


@router.get(
    "/dialogs/{dialog_id}",
    response_model=DialogRead,
    responses=COMMON_ERROR_RESPONSES,
    description=(
        "Возвращает данные диалога по его идентификатору. "
        "Перед ответом проверяется доступ пользователя к диалогу в рамках его аккаунта и проекта."
    ),
)
async def get_dialog(
    dialog_id: str,
    session: DatabaseSession,
    principal: DialogReader,
) -> DialogRead:
    dialog = await _get_dialog(
        session,
        principal=principal,
        dialog_id=dialog_id,
        required_scope="dialogs:read",
    )
    return DialogRead.model_validate(dialog)


@router.get(
    "/dialogs/{dialog_id}/messages",
    response_model=DialogMessagePageResponse,
    responses=COMMON_ERROR_RESPONSES,
    description=(
        "Возвращает страницу сообщений выбранного диалога с курсорной пагинацией. "
        "Позволяет загружать историю частями, не запрашивая всю переписку сразу."
    ),
)
async def list_dialog_messages(
    dialog_id: str,
    session: DatabaseSession,
    principal: DialogReader,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
) -> DialogMessagePageResponse:
    dialog = await _get_dialog(
        session,
        principal=principal,
        dialog_id=dialog_id,
        required_scope="dialogs:read",
    )
    statement = select(ChatMessage).where(
        ChatMessage.project_id == dialog.project_id,
        ChatMessage.session_id == dialog.id,
    )
    if cursor is not None:
        cursor_created_at, cursor_id = _decode_cursor(cursor)
        statement = statement.where(
            or_(
                ChatMessage.created_at < cursor_created_at,
                and_(
                    ChatMessage.created_at == cursor_created_at,
                    ChatMessage.id < cursor_id,
                ),
            )
        )
    messages = (
        await session.scalars(
            statement.order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(
                limit + 1
            )
        )
    ).all()
    has_more = len(messages) > limit
    page = messages[:limit]
    next_cursor = _encode_cursor(page[-1].created_at, page[-1].id) if has_more else None
    return DialogMessagePageResponse(
        items=[message for message in reversed(page)],
        next_cursor=next_cursor,
    )


@router.post(
    "/projects/{project_id}/dialogs",
    response_model=DialogIngressAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses=COMMON_ERROR_RESPONSES,
    description=(
        "Принимает входящее сообщение для проекта и сохраняет событие в Inbox. "
        "Обработка передаётся фоновому worker-у, а повтор запроса с тем же ключом "
        "идемпотентности не создаёт дубликат."
    ),
)
async def accept_dialog_message(
    project_id: str,
    body: DialogIngressRequest,
    session: DatabaseSession,
    principal: DialogWriter,
    idempotency_key: IdempotencyKey,
) -> DialogIngressAccepted:
    async with session.begin():
        await require_project_access(
            session,
            principal,
            project_id,
            permission=ProjectPermission.MANAGE,
            required_scopes=("dialogs:write",),
        )
        channel = await session.scalar(
            select(Channel.channel_id).where(
                Channel.project_id == project_id,
                Channel.channel_id == body.channel_id,
            )
        )
        if channel is None:
            raise LookupError(f"Channel {body.channel_id!r} does not exist")

        async def action() -> dict[str, Any]:
            correlation_id = uuid4().hex
            result = await ingest_channel_event(
                session,
                project_id=project_id,
                channel_id=body.channel_id,
                provider_event_id=idempotency_key,
                event_type="message",
                normalized_event=body.model_dump(mode="json"),
                correlation_id=correlation_id,
            )
            return {
                "status": "accepted",
                "event_id": result.event_id,
                "duplicate": result.duplicate,
            }

        response_data, _ = await execute_idempotently(
            session,
            scope=f"account:{principal.account_id}",
            operation=f"dialogs.ingress:{project_id}",
            key=idempotency_key,
            request_data=body.model_dump(mode="json"),
            action=action,
        )
    return DialogIngressAccepted.model_validate(response_data)


async def _queue_dialog_write(
    session: DatabaseSession,
    *,
    dialog: DialogSession,
    principal: DialogWriter,
    role: Literal["operator", "system"],
    text: str,
    idempotency_key: str,
) -> dict[str, Any]:
    message_id = str(uuid4())
    correlation_id = uuid4().hex
    digest = hashlib.sha256(
        f"{principal.account_id}:{dialog.id}:{role}:{idempotency_key}".encode()
    ).hexdigest()
    job = await enqueue_job(
        session,
        project_id=dialog.project_id,
        queue="inbound_dialog",
        kind="process_dialog_write",
        payload={
            "dialog_id": dialog.id,
            "message_id": message_id,
            "role": role,
            "text": text,
            "author_id": principal.user_id,
            "correlation_id": correlation_id,
        },
        idempotency_key=f"dialog-write:{digest}",
        correlation_id=correlation_id,
    )
    return {"status": "accepted", "job_id": job.id}


async def _submit_dialog_write(
    session: DatabaseSession,
    *,
    principal: DialogWriter,
    dialog_id: str,
    text: str,
    role: Literal["operator", "system"],
    idempotency_key: str,
) -> dict[str, Any]:
    async with session.begin():
        dialog = await _get_dialog(
            session,
            principal=principal,
            dialog_id=dialog_id,
            required_scope="dialogs:write",
        )
        if dialog.state != "active":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Messages cannot be added to a closed dialog",
            )
        response_data, _ = await execute_idempotently(
            session,
            scope=f"account:{principal.account_id}",
            operation=f"dialogs.{role}:{dialog_id}",
            key=idempotency_key,
            request_data={"text": text},
            action=lambda: _queue_dialog_write(
                session,
                dialog=dialog,
                principal=principal,
                role=role,
                text=text,
                idempotency_key=idempotency_key,
            ),
        )
    return response_data


@router.post(
    "/dialogs/{dialog_id}/messages",
    response_model=DialogWriteAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses=COMMON_ERROR_RESPONSES,
    description=(
        "Принимает сообщение оператора для активного диалога. "
        "Запись передаётся фоновой обработке для сохранения и доставки."
    ),
)
async def submit_operator_message(
    dialog_id: str,
    body: DialogTextRequest,
    session: DatabaseSession,
    principal: DialogWriter,
    idempotency_key: IdempotencyKey,
) -> DialogWriteAccepted:
    response_data = await _submit_dialog_write(
        session,
        principal=principal,
        dialog_id=dialog_id,
        text=body.text,
        role="operator",
        idempotency_key=idempotency_key,
    )
    return DialogWriteAccepted.model_validate(response_data)


@router.post(
    "/dialogs/{dialog_id}/notes",
    response_model=DialogWriteAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses=COMMON_ERROR_RESPONSES,
    description=(
        "Добавляет внутреннюю системную заметку в активный диалог. "
        "Заметка сохраняется в истории и не отправляется клиенту как сообщение."
    ),
)
async def submit_system_note(
    dialog_id: str,
    body: DialogTextRequest,
    session: DatabaseSession,
    principal: DialogWriter,
    idempotency_key: IdempotencyKey,
) -> DialogWriteAccepted:
    response_data = await _submit_dialog_write(
        session,
        principal=principal,
        dialog_id=dialog_id,
        text=body.text,
        role="system",
        idempotency_key=idempotency_key,
    )
    return DialogWriteAccepted.model_validate(response_data)
