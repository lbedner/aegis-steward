"""A pre-auth posting: what you set on it carries to the posted row (#323).

Plaid's pending charge posts as a NEW row naming its pre-auth, which is
then tombstoned (``upserts``); a category, a payee, a note, tags or a
split set while it was pending used to go with it. A feed that posts
under the same id keeps them already - the row is updated in place.
"""

from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.adapters.providers import queries
from app.services.finance.models import FinanceTransaction

# A category the bank or nothing set, which yours may replace.
_UNSET_CATEGORY = ("provider", "unset")


async def carry_edits(
    db: AsyncSession, pending: FinanceTransaction, posted: FinanceTransaction
) -> None:
    """What you set on a pre-auth survives its posting (#323): category,
    payee, memo, reviewed and left-out, and the rows hung on it. A posted
    row the feed already filed keeps what the bank said only where you had
    said nothing."""
    if (
        pending.category_source not in _UNSET_CATEGORY
        and posted.category_source in _UNSET_CATEGORY
    ):
        posted.category_id = pending.category_id
        posted.category_source = pending.category_source
        posted.is_user_categorized = pending.is_user_categorized
    posted.merchant_id = pending.merchant_id or posted.merchant_id
    posted.memo = posted.memo or pending.memo
    posted.is_reviewed = posted.is_reviewed or pending.is_reviewed
    posted.excluded_from_reports = (
        posted.excluded_from_reports or pending.excluded_from_reports
    )
    if pending.is_split:
        posted.is_split = True
    if pending.id is not None and posted.id is not None:
        await queries.move_children(db, pending.id, posted.id)
