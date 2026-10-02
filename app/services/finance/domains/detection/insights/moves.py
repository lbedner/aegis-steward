"""Which categories moved this month (#346): the Overview's "What moved".

The overspend alert's on-pace figures (``category_months``) about
everyday spending, ranked by how far this month to date sits from the
typical month. Bills are left out - they are Bills & Income's, and a
mortgage that has not gone out yet on the 2nd has not "moved" - and so
is a category with nothing spent yet this month.
"""

from __future__ import annotations

from datetime import date

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.detection.insights.rules import category_months
from app.services.finance.domains.ledger import queries as ledger_queries
from app.services.finance.schemas import CategoryMove


async def category_moves(
    db: AsyncSession,
    *,
    owner_user_id: int | None,
    today: date,
    limit: int | None = None,
) -> list[CategoryMove]:
    """Categories spent in this month, by the size of their change against
    the typical month, up or down, largest first."""
    rows = [
        row
        for row in await category_months(
            db, owner_user_id=owner_user_id, today=today, everyday=True
        )
        if row.this_month > 0
    ]
    names = await ledger_queries.category_names_by_id(
        db, [row.category_id for row in rows]
    )
    moves = sorted(
        (
            CategoryMove(
                category_id=row.category_id,
                name=names.get(row.category_id, "Uncategorized"),
                this_month=row.this_month,
                last_month=row.last_month,
                typical=row.typical,
            )
            for row in rows
        ),
        key=lambda move: (-abs(move.change), move.name),
    )
    return moves[:limit] if limit else moves
