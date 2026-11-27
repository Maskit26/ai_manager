from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.dialog.runtime import DialogRuntime, ModelTurn
from ai_seller_core.dialog.session_service import (
    MessageInput,
    OutboundIntent,
    commit_internal_message,
    commit_turn,
)
from ai_seller_core.jobs.inbox_handler import (
    InboundCommand,
    build_inbound_event_handler,
)
from ai_seller_core.jobs.worker import JobHandler
from ai_seller_core.persistence.models import ChatMessage, DialogSession, InboxEvent, OutboxMessage
from ai_seller_core.persistence.session_lease import acquire_session_lease


class _ApiEventDecoder:
    def decode(self, event: InboxEvent) -> InboundCommand:
        normalized = event.normalized_event
        try:
            customer_id = normalized["customer_id"]
            external_chat_id = normalized["external_chat_id"]
            text = normalized["text"]
            external_message_id = normalized.get("external_message_id")
        except (AttributeError, KeyError, TypeError) as error:
            raise ValueError("Malformed authenticated dialog event") from error
        if (
            not isinstance(customer_id, str)
            or not isinstance(external_chat_id, str)
            or not isinstance(text, str)
            or (external_message_id is not None and not isinstance(external_message_id, str))
        ):
            raise ValueError("Malformed authenticated dialog event")
        return InboundCommand(
            project_id=event.project_id,
            channel_id=event.channel_id,
            customer_id=customer_id,
            external_chat_id=external_chat_id,
            text=text,
            external_message_id=external_message_id,
        )


class _DevelopmentSanitizer:
    def sanitize(self, text: str) -> str:
        return text


class _DevelopmentProvider:
    async def generate(
        self,
        *,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
    ) -> ModelTurn:
        return ModelTurn(text="Development-mode response.")


class _DevelopmentTools:
    def available_tools(self, *, project_id: str) -> list[dict[str, Any]]:
        return []

    async def execute(self, *, project_id: str, session_id: str, call: Any) -> dict[str, Any]:
        raise RuntimeError("Development worker has no tools configured")


def _development_runtime(project_id: str, release_id: str) -> DialogRuntime:
    sanitizer = _DevelopmentSanitizer()
    return DialogRuntime(
        provider=_DevelopmentProvider(),
        tools=_DevelopmentTools(),
        sanitizer=sanitizer,
    )


def build_development_handlers(
    *,
    worker_id: str,
    lease_seconds: int,
    session_factory: Callable[[], AsyncSession],
) -> dict[str, JobHandler]:
    inbound_handler = build_inbound_event_handler(
        worker_id=worker_id,
        decoder=_ApiEventDecoder(),
        sanitizer=_DevelopmentSanitizer(),
        runtime_for_project=_development_runtime,
        lease_seconds=lease_seconds,
    )

    async def process_dialog_write(payload: dict[str, Any]) -> None:
        project_id = payload.get("project_id")
        dialog_id = payload.get("dialog_id")
        message_id = payload.get("message_id")
        role = payload.get("role")
        text = payload.get("text")
        author_id = payload.get("author_id")
        correlation_id = payload.get("correlation_id")
        if not all(
            isinstance(value, str) and value
            for value in (project_id, dialog_id, message_id, text, author_id, correlation_id)
        ):
            raise ValueError("Malformed dialog write job")
        if role not in {"operator", "system"}:
            raise ValueError("Unsupported dialog write role")

        async with session_factory() as session, session.begin():
            dialog = await session.scalar(
                select(DialogSession)
                .where(
                    DialogSession.project_id == project_id,
                    DialogSession.id == dialog_id,
                )
                .with_for_update()
            )
            if dialog is None:
                raise LookupError(f"Dialog session {dialog_id!r} does not exist")
            lease = await acquire_session_lease(
                session,
                project_id=project_id,
                session_id=dialog_id,
                owner=worker_id,
                lease_seconds=lease_seconds,
            )
            if lease is None:
                raise RuntimeError("Dialog session is currently leased")

            message = MessageInput(
                role=role,
                content={"text": text, "author_id": author_id},
                id=message_id,
            )
            if role == "operator":
                await commit_turn(
                    session,
                    lease=lease,
                    inbound=None,
                    outbound=(message,),
                    outbox=(
                        OutboundIntent(
                            payload={"text": text},
                            idempotency_key=f"operator:{message_id}",
                            correlation_id=correlation_id,
                        ),
                    ),
                    correlation_id=correlation_id,
                )
            else:
                await commit_internal_message(
                    session,
                    lease=lease,
                    message=message,
                    correlation_id=correlation_id,
                )

    async def deliver_fake_outbox(payload: dict[str, Any]) -> None:
        project_id = payload.get("project_id")
        outbox_id = payload.get("outbox_id")
        if not isinstance(project_id, str) or not isinstance(outbox_id, str):
            raise ValueError("Malformed outbox delivery job")

        async with session_factory() as session, session.begin():
            outbox_message = await session.scalar(
                select(OutboxMessage)
                .where(
                    OutboxMessage.project_id == project_id,
                    OutboxMessage.id == outbox_id,
                )
                .with_for_update()
            )
            if outbox_message is None:
                raise LookupError(f"Outbox message {outbox_id!r} does not exist")
            if outbox_message.status == "sent":
                return

            outbox_message.status = "sent"
            outbox_message.attempts += 1
            outbox_message.remote_message_id = f"development:{outbox_message.id}"
            outbox_message.last_error = None
            if outbox_message.chat_message_id is not None:
                chat_message = await session.scalar(
                    select(ChatMessage).where(
                        ChatMessage.project_id == project_id,
                        ChatMessage.session_id == outbox_message.session_id,
                        ChatMessage.id == outbox_message.chat_message_id,
                    )
                )
                if chat_message is None:
                    raise LookupError(
                        f"Chat message {outbox_message.chat_message_id!r} does not exist"
                    )
                chat_message.delivery_status = "sent"

    return {
        "process_inbound_event": inbound_handler,
        "process_dialog_write": process_dialog_write,
        "deliver_outbox_message": deliver_fake_outbox,
    }
