from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.persistence.billing_models import (
    DancoinBalance,
    DancoinReservation,
    DancoinTransaction,
)

_CENT = Decimal("0.01")
_ZERO = Decimal("0.00")
_RESERVATION_LIFETIME = timedelta(hours=24)


class BillingOperationError(ValueError):
    pass


class InsufficientDancoinError(BillingOperationError):
    pass


class OutstandingDancoinDebtError(BillingOperationError):
    pass


class IdempotencyConflictError(BillingOperationError):
    pass


class InvalidReservationStateError(BillingOperationError):
    pass


class ReservationNotExpiredError(BillingOperationError):
    pass


class BillingConsistencyError(BillingOperationError):
    pass


def _require_transaction(db: AsyncSession) -> None:
    if not db.in_transaction():
        raise RuntimeError("Dancoin operations require an active database transaction")


def _amount(value: Decimal, *, positive: bool = True) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("Dancoin amounts must be finite Decimal values")
    try:
        normalized = value.quantize(_CENT)
    except InvalidOperation as error:
        raise ValueError("Dancoin amount is outside the supported range") from error
    if normalized != value:
        raise ValueError("Dancoin amounts must have at most two decimal places")
    if positive and normalized <= _ZERO:
        raise ValueError("Dancoin amount must be greater than zero")
    if not positive and normalized < _ZERO:
        raise ValueError("Dancoin amount cannot be negative")
    return normalized


def _reference(value: str) -> str:
    if not value or not value.strip() or len(value) > 200:
        raise ValueError("Billing references must contain 1 to 200 non-whitespace characters")
    return value


def _operation_reference(operation_id: str, action: str) -> str:
    digest = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
    return f"dancoin:{action}:{digest}"


def _available_balance(
    grant_balance: Decimal,
    purchased_balance: Decimal,
    reserved_grant: Decimal,
    reserved_purchased: Decimal,
    debt_balance: Decimal,
) -> Decimal:
    if debt_balance > _ZERO:
        return _ZERO
    return max(
        _ZERO,
        grant_balance + purchased_balance - reserved_grant - reserved_purchased,
    )


def _balance_after(balance: DancoinBalance, **changes: Decimal) -> Decimal:
    values = {
        "grant_balance": balance.grant_balance,
        "purchased_balance": balance.purchased_balance,
        "reserved_grant": balance.reserved_grant,
        "reserved_purchased": balance.reserved_purchased,
        "debt_balance": balance.debt_balance,
    }
    values.update(changes)
    return _available_balance(**values)


async def _locked_balance(db: AsyncSession, account_id: str) -> DancoinBalance:
    await db.execute(
        insert(DancoinBalance)
        .values(account_id=account_id)
        .on_conflict_do_nothing(index_elements=[DancoinBalance.account_id])
    )
    balance = await db.scalar(
        select(DancoinBalance).where(DancoinBalance.account_id == account_id).with_for_update()
    )
    if balance is None:
        raise BillingConsistencyError("Could not load the account Dancoin balance")
    return balance


async def _find_transaction(db: AsyncSession, reference: str) -> DancoinTransaction | None:
    return await db.scalar(
        select(DancoinTransaction).where(DancoinTransaction.reference == reference)
    )


def _assert_transaction_matches(
    transaction: DancoinTransaction,
    *,
    account_id: str,
    transaction_type: str,
    amount: Decimal,
    source: str | None,
) -> None:
    if (
        transaction.account_id != account_id
        or transaction.type != transaction_type
        or transaction.amount != amount
        or transaction.source != source
    ):
        raise IdempotencyConflictError(
            "The billing reference was already used for a different operation"
        )


async def _record_transaction(
    db: AsyncSession,
    *,
    account_id: str,
    reference: str,
    transaction_type: str,
    source: str | None,
    amount: Decimal,
    balance_after: Decimal,
    details: dict[str, str],
) -> tuple[DancoinTransaction, bool]:
    existing = await _find_transaction(db, reference)
    if existing is not None:
        _assert_transaction_matches(
            existing,
            account_id=account_id,
            transaction_type=transaction_type,
            amount=amount,
            source=source,
        )
        return existing, False

    transaction_id = str(uuid4())
    result = await db.execute(
        insert(DancoinTransaction)
        .values(
            id=transaction_id,
            account_id=account_id,
            reference=reference,
            type=transaction_type,
            source=source,
            amount=amount,
            balance_after=balance_after,
            details=details,
        )
        .on_conflict_do_nothing(index_elements=[DancoinTransaction.reference])
        .returning(DancoinTransaction.id)
    )
    inserted_id = result.scalar_one_or_none()
    if inserted_id is None:
        existing = await _find_transaction(db, reference)
        if existing is None:
            raise BillingConsistencyError("Conflicting ledger entry could not be loaded")
        _assert_transaction_matches(
            existing,
            account_id=account_id,
            transaction_type=transaction_type,
            amount=amount,
            source=source,
        )
        return existing, False

    transaction = await db.get(DancoinTransaction, inserted_id)
    if transaction is None:
        raise BillingConsistencyError("Inserted ledger entry could not be loaded")
    return transaction, True


async def _locked_reservation(
    db: AsyncSession, operation_id: str, account_id: str
) -> DancoinReservation:
    reservation = await db.scalar(
        select(DancoinReservation)
        .where(DancoinReservation.operation_id == operation_id)
        .with_for_update()
    )
    if reservation is None or reservation.account_id != account_id:
        raise BillingConsistencyError("Reservation does not belong to the requested account")
    return reservation


def _assert_reservation_matches(
    reservation: DancoinReservation,
    *,
    account_id: str,
    amount: Decimal,
    expires_at: datetime | None,
) -> None:
    if (
        reservation.account_id != account_id
        or reservation.amount != amount
        or (expires_at is not None and reservation.expires_at != expires_at)
    ):
        raise IdempotencyConflictError(
            "The operation ID was already used for a different reservation"
        )


def _reservation_time(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC) + _RESERVATION_LIFETIME
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Reservation expiration must be timezone-aware")
    return value.astimezone(UTC)


async def grant_dancoin(
    db: AsyncSession,
    *,
    account_id: str,
    reference: str,
    amount: Decimal,
) -> DancoinTransaction:
    _require_transaction(db)
    amount = _amount(amount)
    reference = _reference(reference)
    existing = await _find_transaction(db, reference)
    if existing is not None:
        _assert_transaction_matches(
            existing,
            account_id=account_id,
            transaction_type="grant",
            amount=amount,
            source="grant",
        )
        return existing

    balance = await _locked_balance(db, account_id)
    transaction, inserted = await _record_transaction(
        db,
        account_id=account_id,
        reference=reference,
        transaction_type="grant",
        source="grant",
        amount=amount,
        balance_after=_balance_after(balance, grant_balance=balance.grant_balance + amount),
        details={"operation": "grant"},
    )
    if inserted:
        balance.grant_balance += amount
        await db.flush()
    return transaction


async def purchase_dancoin(
    db: AsyncSession,
    *,
    account_id: str,
    reference: str,
    amount: Decimal,
) -> DancoinTransaction:
    _require_transaction(db)
    amount = _amount(amount)
    reference = _reference(reference)
    existing = await _find_transaction(db, reference)
    if existing is not None:
        _assert_transaction_matches(
            existing,
            account_id=account_id,
            transaction_type="purchase",
            amount=amount,
            source="purchase",
        )
        return existing

    balance = await _locked_balance(db, account_id)
    debt_repaid = min(amount, balance.debt_balance)
    purchased_credit = amount - debt_repaid
    transaction, inserted = await _record_transaction(
        db,
        account_id=account_id,
        reference=reference,
        transaction_type="purchase",
        source="purchase",
        amount=amount,
        balance_after=_balance_after(
            balance,
            purchased_balance=balance.purchased_balance + purchased_credit,
            debt_balance=balance.debt_balance - debt_repaid,
        ),
        details={
            "operation": "purchase",
            "debt_repaid": f"{debt_repaid:.2f}",
        },
    )
    if inserted:
        balance.purchased_balance += purchased_credit
        balance.debt_balance -= debt_repaid
        await db.flush()
    return transaction


async def refund_dancoin(
    db: AsyncSession,
    *,
    account_id: str,
    reference: str,
    amount: Decimal,
    source: str,
) -> DancoinTransaction:
    _require_transaction(db)
    amount = _amount(amount)
    reference = _reference(reference)
    if source not in {"grant", "purchase"}:
        raise ValueError("Refund source must be 'grant' or 'purchase'")
    existing = await _find_transaction(db, reference)
    if existing is not None:
        _assert_transaction_matches(
            existing,
            account_id=account_id,
            transaction_type="refund",
            amount=amount,
            source=source,
        )
        return existing

    balance = await _locked_balance(db, account_id)
    debt_repaid = min(amount, balance.debt_balance) if source == "purchase" else _ZERO
    credited_amount = amount - debt_repaid
    changes = (
        {"grant_balance": balance.grant_balance + credited_amount}
        if source == "grant"
        else {
            "purchased_balance": balance.purchased_balance + credited_amount,
            "debt_balance": balance.debt_balance - debt_repaid,
        }
    )
    transaction, inserted = await _record_transaction(
        db,
        account_id=account_id,
        reference=reference,
        transaction_type="refund",
        source=source,
        amount=amount,
        balance_after=_balance_after(balance, **changes),
        details={
            "operation": "refund",
            "debt_repaid": f"{debt_repaid:.2f}",
        },
    )
    if inserted:
        if source == "grant":
            balance.grant_balance += credited_amount
        else:
            balance.purchased_balance += credited_amount
            balance.debt_balance -= debt_repaid
        await db.flush()
    return transaction


async def spend_dancoin(
    db: AsyncSession,
    *,
    account_id: str,
    reference: str,
    amount: Decimal,
) -> DancoinTransaction:
    _require_transaction(db)
    amount = _amount(amount)
    reference = _reference(reference)
    existing = await _find_transaction(db, reference)
    if existing is not None:
        _assert_transaction_matches(
            existing,
            account_id=account_id,
            transaction_type="spend_agent",
            amount=-amount,
            source=None,
        )
        return existing

    balance = await _locked_balance(db, account_id)
    if balance.debt_balance > _ZERO:
        raise OutstandingDancoinDebtError("Outstanding Dancoin debt blocks spending")
    grant_available = balance.grant_balance - balance.reserved_grant
    purchased_available = balance.purchased_balance - balance.reserved_purchased
    available = grant_available + purchased_available
    if amount > available:
        raise InsufficientDancoinError(
            f"Insufficient Dancoin: requested {amount:.2f}, available {available:.2f}"
        )
    grant_charge = min(amount, grant_available)
    purchased_charge = amount - grant_charge
    transaction, inserted = await _record_transaction(
        db,
        account_id=account_id,
        reference=reference,
        transaction_type="spend_agent",
        source=None,
        amount=-amount,
        balance_after=_balance_after(
            balance,
            grant_balance=balance.grant_balance - grant_charge,
            purchased_balance=balance.purchased_balance - purchased_charge,
        ),
        details={
            "operation": "spend_agent",
            "grant_amount": f"{grant_charge:.2f}",
            "purchased_amount": f"{purchased_charge:.2f}",
        },
    )
    if inserted:
        balance.grant_balance -= grant_charge
        balance.purchased_balance -= purchased_charge
        await db.flush()
    return transaction


async def reserve_dancoin(
    db: AsyncSession,
    *,
    account_id: str,
    operation_id: str,
    amount: Decimal,
    expires_at: datetime | None = None,
) -> DancoinReservation:
    _require_transaction(db)
    amount = _amount(amount)
    operation_id = _reference(operation_id)
    if expires_at is not None:
        expires_at = _reservation_time(expires_at)

    existing = await db.scalar(
        select(DancoinReservation).where(DancoinReservation.operation_id == operation_id)
    )
    if existing is not None:
        _assert_reservation_matches(
            existing,
            account_id=account_id,
            amount=amount,
            expires_at=expires_at,
        )
        return existing

    balance = await _locked_balance(db, account_id)
    existing = await db.scalar(
        select(DancoinReservation)
        .where(DancoinReservation.operation_id == operation_id)
        .with_for_update()
    )
    if existing is not None:
        _assert_reservation_matches(
            existing,
            account_id=account_id,
            amount=amount,
            expires_at=expires_at,
        )
        return existing

    if balance.debt_balance > _ZERO:
        raise OutstandingDancoinDebtError("Outstanding Dancoin debt blocks reservations")
    available = _available_balance(
        balance.grant_balance,
        balance.purchased_balance,
        balance.reserved_grant,
        balance.reserved_purchased,
        balance.debt_balance,
    )
    if amount > available:
        raise InsufficientDancoinError(
            f"Insufficient Dancoin: requested {amount:.2f}, available {available:.2f}"
        )
    grant_available = balance.grant_balance - balance.reserved_grant
    grant_amount = min(amount, grant_available)
    purchased_amount = amount - grant_amount
    expiry = _reservation_time(expires_at)
    reservation_id = str(uuid4())
    result = await db.execute(
        insert(DancoinReservation)
        .values(
            id=reservation_id,
            account_id=account_id,
            operation_id=operation_id,
            amount=amount,
            grant_amount=grant_amount,
            purchased_amount=purchased_amount,
            status="reserved",
            expires_at=expiry,
        )
        .on_conflict_do_nothing(index_elements=[DancoinReservation.operation_id])
        .returning(DancoinReservation.id)
    )
    inserted_id = result.scalar_one_or_none()
    if inserted_id is None:
        existing = await db.scalar(
            select(DancoinReservation).where(DancoinReservation.operation_id == operation_id)
        )
        if existing is None:
            raise BillingConsistencyError("Conflicting reservation could not be loaded")
        _assert_reservation_matches(
            existing,
            account_id=account_id,
            amount=amount,
            expires_at=expires_at,
        )
        return existing

    transaction, inserted = await _record_transaction(
        db,
        account_id=account_id,
        reference=_operation_reference(operation_id, "reserve"),
        transaction_type="reserve",
        source=None,
        amount=-amount,
        balance_after=_balance_after(
            balance,
            reserved_grant=balance.reserved_grant + grant_amount,
            reserved_purchased=balance.reserved_purchased + purchased_amount,
        ),
        details={
            "operation_id": operation_id,
            "grant_amount": f"{grant_amount:.2f}",
            "purchased_amount": f"{purchased_amount:.2f}",
        },
    )
    if not inserted:
        raise BillingConsistencyError("Reservation exists without a matching reserve ledger entry")
    balance.reserved_grant += grant_amount
    balance.reserved_purchased += purchased_amount
    await db.flush()
    reservation = await db.get(DancoinReservation, inserted_id)
    if reservation is None:
        raise BillingConsistencyError("Inserted reservation could not be loaded")
    return reservation


async def settle_reservation(
    db: AsyncSession,
    *,
    account_id: str,
    operation_id: str,
    actual_amount: Decimal,
) -> DancoinTransaction:
    _require_transaction(db)
    operation_id = _reference(operation_id)
    actual_amount = _amount(actual_amount, positive=False)
    reservation = await db.scalar(
        select(DancoinReservation).where(DancoinReservation.operation_id == operation_id)
    )
    if reservation is None or reservation.account_id != account_id:
        raise BillingConsistencyError("Reservation does not belong to the requested account")
    balance = await _locked_balance(db, account_id)
    reservation = await _locked_reservation(db, operation_id, account_id)
    reference = _operation_reference(operation_id, "settle")

    if reservation.status == "settled":
        existing = await _find_transaction(db, reference)
        if existing is None:
            raise BillingConsistencyError("Settled reservation has no settlement ledger entry")
        no_charge = actual_amount == _ZERO
        _assert_transaction_matches(
            existing,
            account_id=account_id,
            transaction_type="release" if no_charge else "settle",
            amount=reservation.amount if no_charge else -actual_amount,
            source=None,
        )
        return existing
    if reservation.status != "reserved":
        raise InvalidReservationStateError(
            f"Cannot settle a reservation in state {reservation.status!r}"
        )
    if actual_amount > reservation.amount:
        raise ValueError("Actual Dancoin cost cannot exceed the reserved maximum")

    grant_charge = min(actual_amount, reservation.grant_amount)
    purchased_charge = actual_amount - grant_charge
    no_charge = actual_amount == _ZERO
    transaction, inserted = await _record_transaction(
        db,
        account_id=account_id,
        reference=reference,
        transaction_type="release" if no_charge else "settle",
        source=None,
        amount=reservation.amount if no_charge else -actual_amount,
        balance_after=_balance_after(
            balance,
            grant_balance=balance.grant_balance - grant_charge,
            purchased_balance=balance.purchased_balance - purchased_charge,
            reserved_grant=balance.reserved_grant - reservation.grant_amount,
            reserved_purchased=balance.reserved_purchased - reservation.purchased_amount,
        ),
        details={
            "operation_id": operation_id,
            "actual_amount": f"{actual_amount:.2f}",
            "grant_amount": f"{grant_charge:.2f}",
            "purchased_amount": f"{purchased_charge:.2f}",
        },
    )
    if not inserted:
        raise BillingConsistencyError("Reservation is active but has a settlement ledger entry")
    balance.grant_balance -= grant_charge
    balance.purchased_balance -= purchased_charge
    balance.reserved_grant -= reservation.grant_amount
    balance.reserved_purchased -= reservation.purchased_amount
    reservation.status = "settled"
    reservation.settled_at = datetime.now(UTC)
    await db.flush()
    return transaction


async def release_reservation(
    db: AsyncSession,
    *,
    account_id: str,
    operation_id: str,
) -> DancoinTransaction:
    _require_transaction(db)
    operation_id = _reference(operation_id)
    reservation = await db.scalar(
        select(DancoinReservation).where(DancoinReservation.operation_id == operation_id)
    )
    if reservation is None or reservation.account_id != account_id:
        raise BillingConsistencyError("Reservation does not belong to the requested account")
    balance = await _locked_balance(db, account_id)
    reservation = await _locked_reservation(db, operation_id, account_id)
    reference = _operation_reference(operation_id, "release")

    if reservation.status == "released":
        existing = await _find_transaction(db, reference)
        if existing is None:
            raise BillingConsistencyError("Released reservation has no release ledger entry")
        _assert_transaction_matches(
            existing,
            account_id=account_id,
            transaction_type="release",
            amount=reservation.amount,
            source=None,
        )
        return existing
    if reservation.status != "reserved":
        raise InvalidReservationStateError(
            f"Cannot release a reservation in state {reservation.status!r}"
        )

    transaction, inserted = await _record_transaction(
        db,
        account_id=account_id,
        reference=reference,
        transaction_type="release",
        source=None,
        amount=reservation.amount,
        balance_after=_balance_after(
            balance,
            reserved_grant=balance.reserved_grant - reservation.grant_amount,
            reserved_purchased=balance.reserved_purchased - reservation.purchased_amount,
        ),
        details={
            "operation_id": operation_id,
            "grant_amount": f"{reservation.grant_amount:.2f}",
            "purchased_amount": f"{reservation.purchased_amount:.2f}",
        },
    )
    if not inserted:
        raise BillingConsistencyError("Reservation is active but has a release ledger entry")
    balance.reserved_grant -= reservation.grant_amount
    balance.reserved_purchased -= reservation.purchased_amount
    reservation.status = "released"
    reservation.settled_at = datetime.now(UTC)
    await db.flush()
    return transaction


async def timeout_reservation(
    db: AsyncSession,
    *,
    account_id: str,
    operation_id: str,
    now: datetime | None = None,
) -> DancoinTransaction:
    _require_transaction(db)
    operation_id = _reference(operation_id)
    now = now or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Timeout check time must be timezone-aware")
    now = now.astimezone(UTC)
    reservation = await db.scalar(
        select(DancoinReservation).where(DancoinReservation.operation_id == operation_id)
    )
    if reservation is None or reservation.account_id != account_id:
        raise BillingConsistencyError("Reservation does not belong to the requested account")
    balance = await _locked_balance(db, account_id)
    reservation = await _locked_reservation(db, operation_id, account_id)
    reference = _operation_reference(operation_id, "timeout")

    if reservation.status == "timeout_charged":
        existing = await _find_transaction(db, reference)
        if existing is None:
            raise BillingConsistencyError("Timed-out reservation has no timeout ledger entry")
        _assert_transaction_matches(
            existing,
            account_id=account_id,
            transaction_type="timeout_charge",
            amount=-reservation.amount,
            source=None,
        )
        return existing
    if reservation.status != "reserved":
        raise InvalidReservationStateError(
            f"Cannot timeout-charge a reservation in state {reservation.status!r}"
        )
    if reservation.expires_at is None or reservation.expires_at > now:
        raise ReservationNotExpiredError("Reservation has not reached its timeout")

    transaction, inserted = await _record_transaction(
        db,
        account_id=account_id,
        reference=reference,
        transaction_type="timeout_charge",
        source=None,
        amount=-reservation.amount,
        balance_after=_balance_after(
            balance,
            grant_balance=balance.grant_balance - reservation.grant_amount,
            purchased_balance=balance.purchased_balance - reservation.purchased_amount,
            reserved_grant=balance.reserved_grant - reservation.grant_amount,
            reserved_purchased=balance.reserved_purchased - reservation.purchased_amount,
        ),
        details={
            "operation_id": operation_id,
            "grant_amount": f"{reservation.grant_amount:.2f}",
            "purchased_amount": f"{reservation.purchased_amount:.2f}",
        },
    )
    if not inserted:
        raise BillingConsistencyError("Reservation is active but has a timeout-charge ledger entry")
    balance.grant_balance -= reservation.grant_amount
    balance.purchased_balance -= reservation.purchased_amount
    balance.reserved_grant -= reservation.grant_amount
    balance.reserved_purchased -= reservation.purchased_amount
    reservation.status = "timeout_charged"
    reservation.settled_at = now
    await db.flush()
    return transaction


async def compensate_timeout_charge(
    db: AsyncSession,
    *,
    account_id: str,
    operation_id: str,
    actual_amount: Decimal,
) -> DancoinTransaction | None:
    _require_transaction(db)
    operation_id = _reference(operation_id)
    actual_amount = _amount(actual_amount, positive=False)
    reservation = await db.scalar(
        select(DancoinReservation).where(DancoinReservation.operation_id == operation_id)
    )
    if reservation is None or reservation.account_id != account_id:
        raise BillingConsistencyError("Reservation does not belong to the requested account")
    balance = await _locked_balance(db, account_id)
    reservation = await _locked_reservation(db, operation_id, account_id)
    if reservation.status != "timeout_charged":
        raise InvalidReservationStateError(
            "Late usage correction requires a timeout-charged reservation"
        )
    if actual_amount == reservation.amount:
        return None

    delta = reservation.amount - actual_amount
    reference = _operation_reference(operation_id, "timeout-correction")
    existing = await _find_transaction(db, reference)
    if existing is not None:
        _assert_transaction_matches(
            existing,
            account_id=account_id,
            transaction_type="adjustment",
            amount=delta,
            source=None,
        )
        return existing

    if delta > _ZERO:
        actual_grant = min(actual_amount, reservation.grant_amount)
        actual_purchased = actual_amount - actual_grant
        grant_refund = reservation.grant_amount - actual_grant
        purchased_refund = reservation.purchased_amount - actual_purchased
        debt_repaid = min(balance.debt_balance, purchased_refund)
        new_grant = balance.grant_balance + grant_refund
        new_purchased = balance.purchased_balance + purchased_refund - debt_repaid
        new_debt = balance.debt_balance - debt_repaid
    else:
        extra_charge = -delta
        grant_available = balance.grant_balance - balance.reserved_grant
        purchased_available = balance.purchased_balance - balance.reserved_purchased
        grant_charge = min(extra_charge, grant_available)
        purchased_charge = min(extra_charge - grant_charge, purchased_available)
        new_grant = balance.grant_balance - grant_charge
        new_purchased = balance.purchased_balance - purchased_charge
        new_debt = balance.debt_balance + extra_charge - grant_charge - purchased_charge
        grant_refund = _ZERO
        purchased_refund = _ZERO
        debt_repaid = _ZERO

    transaction, inserted = await _record_transaction(
        db,
        account_id=account_id,
        reference=reference,
        transaction_type="adjustment",
        source=None,
        amount=delta,
        balance_after=_balance_after(
            balance,
            grant_balance=new_grant,
            purchased_balance=new_purchased,
            debt_balance=new_debt,
        ),
        details={
            "operation_id": operation_id,
            "actual_amount": f"{actual_amount:.2f}",
            "grant_refund": f"{grant_refund:.2f}",
            "purchased_refund": f"{purchased_refund:.2f}",
            "debt_repaid": f"{debt_repaid:.2f}",
        },
    )
    if inserted:
        balance.grant_balance = new_grant
        balance.purchased_balance = new_purchased
        balance.debt_balance = new_debt
        await db.flush()
    return transaction


async def expire_grant(
    db: AsyncSession,
    *,
    account_id: str,
    reference: str,
    amount: Decimal,
) -> DancoinTransaction:
    _require_transaction(db)
    amount = _amount(amount)
    reference = _reference(reference)
    existing = await _find_transaction(db, reference)
    if existing is not None:
        _assert_transaction_matches(
            existing,
            account_id=account_id,
            transaction_type="expire",
            amount=-amount,
            source="grant",
        )
        return existing

    balance = await _locked_balance(db, account_id)
    grant_available = balance.grant_balance - balance.reserved_grant
    if amount > grant_available:
        raise InsufficientDancoinError(
            f"Insufficient unreserved grant balance: requested {amount:.2f}, "
            f"available {grant_available:.2f}"
        )
    transaction, inserted = await _record_transaction(
        db,
        account_id=account_id,
        reference=reference,
        transaction_type="expire",
        source="grant",
        amount=-amount,
        balance_after=_balance_after(balance, grant_balance=balance.grant_balance - amount),
        details={"operation": "expire_grant"},
    )
    if inserted:
        balance.grant_balance -= amount
        await db.flush()
    return transaction
