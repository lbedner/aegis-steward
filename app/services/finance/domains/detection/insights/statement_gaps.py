"""A statement on file checks the register (#321).

A filed statement's closing balance is compared with the register on its
date, the comparison Correct the balance makes, without anybody asking.
A gap needs attention, and opens that correction filled in; a match
reconciles the account through the statement's date. A correction
answers every gap up to the date it reconciles to.
"""

from __future__ import annotations

from datetime import date

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.detection import queries
from app.services.finance.domains.detection.insights.formatting import format_usd
from app.services.finance.domains.detection.insights.rules.shared import (
    create_insight_if_new,
)
from app.services.finance.domains.ledger.accounts import (
    mark_reconciled,
    reconcile_preview,
    register_balance_as_of,
    statement_balance,
)
from app.services.finance.models import FinanceAccount, FinanceInsight
from app.services.finance.utils import utcnow
from app.services.shared.queries import stored_owner

GAP = "statement_gap"


async def check_statement(
    db: AsyncSession, account: FinanceAccount, *, statement_date: date, printed: int
) -> int:
    """How far the register is from a statement's ``printed`` balance on
    ``statement_date`` (what a debt's statement prints is owed). 0 when
    they agree, or the account keeps no register to compare."""
    assert account.id is not None
    balance = statement_balance(account.classification, printed)
    preview = await reconcile_preview(
        db,
        account.id,
        owner_user_id=account.owner_user_id,
        statement_date=statement_date,
        statement_balance=balance,
    )
    if preview is None or preview.route != "adjustment":
        return 0
    # The register as it stands, a correction already on that day
    # included: the preview leaves it out (a correction replaces it),
    # which reads a correction the missing row made stale as a match.
    register = await register_balance_as_of(db, account.id, statement_date)
    gap = balance - register
    if gap == 0:
        mark_reconciled(account, statement_date)
        db.add(account)
        await settle_gaps(db, account.id, statement_date)
        return 0
    await create_insight_if_new(
        db,
        owner_user_id=stored_owner(account.owner_user_id),
        insight_type=GAP,
        dedup_key=f"{GAP}:{account.id}:{statement_date.isoformat()}",
        severity="warning",
        title=(f"{account.name} is {format_usd(abs(gap))} off its statement"),
        body=(
            f"The statement for {statement_date:%b %-d, %Y} says "
            f"{format_usd(printed)}; the register says "
            f"{format_usd(statement_balance(account.classification, register))}. "
            "Something is missing or counted twice: correct the balance, "
            "or find it."
        ),
        # As printed: what Correct the balance is filled in with, the way
        # its form asks (a debt as what is owed).
        detected_amount=printed,
        related_account_id=account.id,
        period_end=statement_date,
    )
    return gap


async def settle_gaps(db: AsyncSession, account_id: int, through: date) -> None:
    """The account's open gaps up to ``through``, answered: it reconciles
    there now."""
    rows = await queries.insight_rows_where(
        db,
        [
            FinanceInsight.insight_type == GAP,
            FinanceInsight.related_account_id == account_id,
            FinanceInsight.status == "new",
            FinanceInsight.period_end <= through,
        ],
    )
    for row in rows:
        row.status, row.resolution, row.resolved_at = "actioned", "resolved", utcnow()
        db.add(row)
