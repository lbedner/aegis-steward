"""The shared card subject: every describe renders the same
transaction the same way."""

from __future__ import annotations

from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.formatting import format_date, payee_label
from app.services.finance.domains.detection.insights.formatting import format_usd
from app.services.finance.domains.ledger import transactions
from app.services.finance.schemas import ChangeDisplayRow


async def txn_subject(
    db: AsyncSession, transaction_id: int, owner_user_id: int | None
) -> tuple[Any, str]:
    """The row and its card-ready one-liner: the register's curated
    payee (never the raw bank descriptor), amount, date. Every
    describe renders the same subject the same way.

    This said "never the raw bank descriptor" while reading
    ``merchant_name or name`` - the SOURCE's merchant and then the
    descriptor, with the payee the user named skipped entirely. So a
    card proposing a change to a renamed row asked about
    "GREENSKY WEB PAY GREE NSKY WEB ID: 2274797123" and the register
    two clicks away called the same row GreenSky. ``payee_label`` is the
    one rule and this is the last surface that was not using it.
    """
    txn = await transactions.get_transaction(
        db, transaction_id, owner_user_id=owner_user_id
    )
    if txn is None:
        return None, f"transaction {transaction_id} (missing)"
    return txn, (
        f"{await payee_of(db, txn)} "
        f"({format_usd(abs(txn.amount))} on {format_date(txn.date_)})"
    )


async def txn_row(
    db: AsyncSession, transaction_id: int, owner_user_id: int | None
) -> tuple[Any, ChangeDisplayRow]:
    """The subject line as a display row, carrying its own figures.

    Every describe starts with this line; a card that draws many of them
    can add them up because the amount and the date ride beside the
    sentence instead of only inside it.
    """
    txn, subject = await txn_subject(db, transaction_id, owner_user_id)
    return txn, ChangeDisplayRow(
        label="Transaction",
        value=subject,
        amount=abs(txn.amount) if txn is not None else None,
        at=txn.date_.isoformat() if txn is not None else None,
    )


async def payee_of(db: AsyncSession, txn: Any) -> str:
    """What this row is CALLED, by the one rule every surface uses."""
    from app.core.formatting import payee_label
    from app.services.finance.domains.ledger.merchants import merchant_names

    named = (
        (await merchant_names(db, [txn.merchant_id])).get(txn.merchant_id)
        if txn.merchant_id
        else None
    )
    return payee_label(named, txn.merchant_name, txn.name)


def candidate_row(txn: Any) -> dict[str, Any]:
    """A transaction as a match shortlist names it: the id a proposal
    takes, and enough to recognise it. Bills and claims read one shape."""
    return {
        "id": txn.id,
        "date": txn.date_.isoformat(),
        "payee": payee_label(None, txn.merchant_name, txn.name),
        "amount": txn.amount,
        "account_id": txn.account_id,
    }
