"""A card bill expected at its statement balance (#341).

A bill's amount is read off its last year of payments, which for a card
paid in full each month is an average of statements that are all gone:
the one that matters is the latest, and it prints what is due and when.

Only a card paid IN FULL takes it - its payment varies with the
statement. One paid a fixed $250 a month keeps its $250: the statement's
$7,857 is not what leaves the account.
"""

from __future__ import annotations

from datetime import date

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.detection.queries import streams_by_ids
from app.services.finance.domains.ledger.queries.accounts import (
    liability_details_by_account,
)
from app.services.finance.domains.planning.recurring import queries
from app.services.finance.models import FinanceAccount, FinanceLiabilityDetail
from app.services.finance.utils import utcnow


async def record_statement(
    db: AsyncSession,
    card: FinanceAccount,
    *,
    balance: int,
    issued: date,
    due: date | None = None,
    minimum: int | None = None,
) -> bool:
    """Keep the card's latest statement, and set the payment it is due on
    each bill that pays the card in full. False for a statement older
    than the one already kept: an old one filed late changes nothing."""
    assert card.id is not None
    detail = (await liability_details_by_account(db, [card.id])).get(card.id)
    if detail is None:
        detail = FinanceLiabilityDetail(
            owner_user_id=card.owner_user_id,
            account_id=card.id,
            liability_type="credit",
        )
    kept = detail.last_statement_issue_date
    if kept is not None and issued <= kept:
        return False
    detail.last_statement_balance = balance
    detail.last_statement_issue_date = issued
    if due is not None:
        detail.next_payment_due_date = due
    if minimum is not None:
        detail.minimum_payment_amount = minimum
    detail.updated_at = utcnow()
    db.add(detail)
    for stream in await streams_by_ids(db, await queries.streams_paying(db, card.id)):
        if stream.deleted_at is not None or not stream.amount_is_variable:
            continue
        # Paid since it was issued: the statement's payment is behind it,
        # and the next one waits for the next statement.
        if stream.last_date is not None and stream.last_date >= issued:
            continue
        stream.expected_amount = balance
        if due is not None:
            stream.next_expected_date = due
        db.add(stream)
    await db.flush()
    return True
