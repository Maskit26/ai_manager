from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select

from ai_seller_core.persistence.base import SessionFactory
from ai_seller_core.persistence.models import OutboxMessage


class ChannelSender(Protocol):
    async def send(
        self,
        *,
        channel_id: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> str | None: ...


class DeliveryPolicy(Protocol):
    async def may_send(self, *, message: OutboxMessage) -> bool: ...


@dataclass(frozen=True)
class DeliveryUnknownError(Exception):
    """The remote side may have accepted the request; unsafe retries are forbidden."""


class OutboxDelivery:
    def __init__(self, *, sender: ChannelSender, policy: DeliveryPolicy) -> None:
        self.sender = sender
        self.policy = policy

    async def deliver(self, payload: dict[str, Any]) -> None:
        outbox_id = str(payload["outbox_id"])
        async with SessionFactory() as session, session.begin():
            message = await session.scalar(
                select(OutboxMessage)
                .where(OutboxMessage.id == outbox_id)
                .with_for_update()
            )
            if message is None or message.status in {
                "delivered",
                "delivery_unknown",
                "failed",
                "expired",
                "held",
            }:
                return
            if message.deadline_at is not None and message.deadline_at <= datetime.now(UTC):
                message.status = "expired"
                return
            if not await self.policy.may_send(message=message):
                message.status = "held"
                message.last_error = "Delivery policy blocked the message"
                return
            message.status = "sending"
            message.attempts += 1
            channel_id = message.channel_id
            body = message.payload
            key = message.idempotency_key

        try:
            remote_id = await self.sender.send(
                channel_id=channel_id,
                payload=body,
                idempotency_key=key,
            )
        except DeliveryUnknownError as error:
            await self._set_status(outbox_id, "delivery_unknown", str(error))
            return
        except Exception as error:
            await self._set_status(outbox_id, "retrying", str(error))
            raise
        await self._set_status(outbox_id, "delivered", remote_id=remote_id)

    @staticmethod
    async def _set_status(
        outbox_id: str,
        status: str,
        error: str | None = None,
        remote_id: str | None = None,
    ) -> None:
        async with SessionFactory() as session, session.begin():
            message = await session.scalar(
                select(OutboxMessage)
                .where(OutboxMessage.id == outbox_id)
                .with_for_update()
            )
            if message is None:
                raise LookupError(f"Outbox message {outbox_id!r} does not exist")
            message.status = status
            message.last_error = error[:2000] if error else None
            message.remote_message_id = remote_id
