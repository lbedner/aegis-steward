"""A pre-auth posting: what you set on it carries to the posted row (#323).

Plaid's pending charge posts as a NEW row naming its pre-auth, which is
then tombstoned (``upserts``); a category, a payee, a note, tags or a
split set while it was pending used to go with it. A feed that posts
under the same id keeps them already - the row is updated in place.

SimpleFIN posts under a new id and names no pre-auth (#540): its pending
row stayed live beside the posted one, and the charge counted twice. A
feed that reports every row it holds says a pending row has posted, or
dropped, by no longer reporting it (``retire_unreported``).
"""

from __future__ import annotations

from datetime import date

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.adapters.providers import queries
from app.services.finance.domains.ledger.queries.filters import is_duplicate
from app.services.finance.models import FinanceTransaction
from app.services.finance.utils import normalize_payee

# A category the bank or nothing set, which yours may replace.
_UNSET_CATEGORY = ("provider", "unset")

# How long after its pre-auth a charge posts: a hold lasts days, not weeks.
TWIN_DAYS = 7
# A hold can settle for another amount - a tip on, a gas pre-auth off: the
# same name within this share of it is the same charge.
SETTLE_SHARE = 0.3
# A pending row the feed stops listing, with no twin, is kept this many
# days: a feed can leave a row out of one pass.
GRACE_DAYS = 3


async def carry_edits(
    db: AsyncSession, pending: FinanceTransaction, posted: FinanceTransaction
) -> None:
    """What you set on a pre-auth survives its posting (#323): category,
    payee, memo, bill, reviewed and left-out, and the rows hung on it. A
    posted row the feed already filed keeps what the bank said only where
    you had said nothing. A split carries only to a charge that settled for
    the same amount: its lines would add up to the hold, not the charge."""
    if (
        pending.category_source not in _UNSET_CATEGORY
        and posted.category_source in _UNSET_CATEGORY
    ):
        posted.category_id = pending.category_id
        posted.category_source = pending.category_source
        posted.is_user_categorized = pending.is_user_categorized
    posted.merchant_id = pending.merchant_id or posted.merchant_id
    posted.memo = posted.memo or pending.memo
    # The bill it pays: matched while pending, it is still that bill.
    posted.recurring_stream_id = (
        posted.recurring_stream_id or pending.recurring_stream_id
    )
    posted.is_reviewed = posted.is_reviewed or pending.is_reviewed
    # Left out by you, not by a pairing: a pair is the pre-auth's, and goes
    # with it (``retire``); detection pairs the posted row on its own.
    posted.excluded_from_reports = posted.excluded_from_reports or (
        pending.excluded_from_reports and not pending.is_transfer
    )
    lines = pending.is_split and pending.amount == posted.amount
    posted.is_split = posted.is_split or lines
    if pending.id is not None and posted.id is not None:
        await queries.move_children(db, pending.id, posted.id, lines=lines)


async def retire_unreported(
    db: AsyncSession, *, source: str, reported: set[tuple[int, str]], today: date
) -> int:
    """Every live pending row on the accounts the feed reported rows for,
    that it no longer reports: posted under an id of its own, or dropped.
    One with a twin (``_twin``) hands it what was set on it and goes; one
    without waits ``GRACE_DAYS`` - a feed can leave a row out for a pass.
    ``reported`` is ``(account, external id)`` of every row the feed
    sent. How many went."""
    accounts = {account_id for account_id, _external_id in reported}
    gone = [
        row
        for row in await queries.live_pending_rows(
            db, account_ids=accounts, source=source
        )
        if (row.account_id, row.external_id) not in reported
    ]
    if not gone:
        return 0
    twins = await queries.unlinked_posted_rows(
        db, account_ids=accounts, source=source, since=min(r.date_ for r in gone)
    )
    retiring: list[int] = []
    for row in sorted(gone, key=lambda r: (r.date_, r.id or 0)):
        twin = _twin(row, twins)
        if twin is not None:
            twins.remove(twin)
            twin.pending_transaction_id = row.id
            # The bank's copy of an export's row is out of view: the export
            # row is the one you keep, so nothing is carried to the copy.
            if not is_duplicate(twin):
                await carry_edits(db, row, twin)
            db.add(twin)
        elif (today - row.date_).days < GRACE_DAYS:
            continue
        if row.id is not None:
            retiring.append(row.id)
    await retire(db, retiring)
    return len(retiring)


def _twin(
    pending: FinanceTransaction, posted: list[FinanceTransaction]
) -> FinanceTransaction | None:
    """The row ``pending`` posted as: made after it, on its account, within
    ``TWIN_DAYS`` after it - for the same amount, else under the same name
    within ``SETTLE_SHARE`` of it. Nearest first. A row that was already
    there is another charge, however alike."""
    later = [
        row
        for row in posted
        if row.account_id == pending.account_id
        and (row.id or 0) > (pending.id or 0)
        and 0 <= (row.date_ - pending.date_).days <= TWIN_DAYS
        and (row.amount < 0) == (pending.amount < 0)
    ]
    exact = [row for row in later if row.amount == pending.amount]
    called = normalize_payee(pending.name or "")
    settled = [
        row
        for row in later
        if called
        and normalize_payee(row.name or "") == called
        and abs(row.amount - pending.amount) <= abs(pending.amount) * SETTLE_SHARE
    ]
    return min(exact or settled, key=lambda r: (r.date_, r.id or 0), default=None)


async def retire(db: AsyncSession, pending_ids: list[int]) -> None:
    """Pre-auths that posted or dropped, deleted the way any row is, and a
    pair on one with them: its other leg counts again, and detection pairs
    it with what posted (#540) - a pair left on a deleted pre-auth claims
    its other leg for good. Imported lazily: the ledger sits under the
    feeds."""
    from app.services.finance.domains.detection.transfers.offers import (
        drop_pairs_on,
    )
    from app.services.finance.domains.ledger.transactions import (
        soft_delete_transactions,
    )

    if pending_ids:
        await soft_delete_transactions(db, pending_ids)
        await drop_pairs_on(db, pending_ids)
