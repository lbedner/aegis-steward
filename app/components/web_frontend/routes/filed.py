"""A place's filed transactions, as its page draws them (#290, #303).

What a case cost and what was paid a contact read one way: the charges
filed under the place's label (``transaction.link``), shaped as every
transaction surface shapes them, and their signed total.
"""

from __future__ import annotations

from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.components.web_frontend.routes.finance.overview import TXN_COLUMNS
from app.services.finance.domains.ledger import links
from app.services.finance.domains.ledger.hydrate import hydrate_transactions
from app.services.finance.service import FinanceService


async def filed(db: AsyncSession, label: str) -> dict[str, Any]:
    """``filed_card``'s context: the rows, the columns and the total."""
    txns = (await links.linked(db, [label]))[label]
    return {
        "rows": await hydrate_transactions(FinanceService(db), txns),
        "columns": TXN_COLUMNS,
        "total": sum(t.amount for t in txns),
    }
