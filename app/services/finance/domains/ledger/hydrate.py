"""A transaction as every surface shows it.

One hydration for the register, the uncategorized queue, a single
re-rendered row, the budget drilldown and the agent's own lookup: rows
in, response items out, with category and payee names, icons, tags and
split lines attached.

It lived in the API router that happened to need it first, which made
every other caller reach into ``app.components.backend`` for it - the
web routes, and the AI tools, which are a service. A service importing
a router inverts the layering CLAUDE.md sets out and drags FastAPI in
behind it. Nothing here touches HTTP; it takes a service and returns
schemas, so it belongs with the service.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.finance.models import FinanceTransactionSplit
from app.services.finance.schemas import (
    SplitLineResponse,
    TagRef,
    TransactionResponse,
)

if TYPE_CHECKING:
    from app.services.finance.models import FinanceTransaction
    from app.services.finance.service import FinanceService


def split_line(
    split: FinanceTransactionSplit, names: dict[int, str]
) -> SplitLineResponse:
    """A split row as its response shape, category name resolved."""
    return SplitLineResponse(
        id=split.id,
        amount=split.amount,
        category_id=split.category_id,
        category=names.get(split.category_id),
        memo=split.memo,
    )


async def hydrate_transactions(
    service: FinanceService, transactions: list[FinanceTransaction]
) -> list[TransactionResponse]:
    """Rows -> API items with category and payee names, icons, tags and
    split lines attached. Every surface that shows a transaction (the
    register, the uncategorized queue, a single re-rendered row) reads the
    same shape from here."""
    splits_by_txn = await service.transaction_splits(
        [t.id for t in transactions if t.is_split and t.id is not None]
    )
    names = await service.category_names(
        {t.category_id for t in transactions if t.category_id is not None}
        | {
            s.category_id
            for lines in splits_by_txn.values()
            for s in lines
            if s.category_id is not None
        }
    )
    payees = await service.merchant_names(
        {t.merchant_id for t in transactions if t.merchant_id is not None}
    )
    from app.services.finance.domains.ledger.merchant_icon import payee_icons

    # Payee first: the raw descriptor is a bank string, the payee is the
    # thing with a brand.
    icons = await payee_icons(
        service.db,
        [(t.merchant_id, payees.get(t.merchant_id) or t.name) for t in transactions],
    )
    tags_by_txn = await service.transaction_tags(
        {t.id for t in transactions if t.id is not None}
    )
    usual = await service.merchant_usual_categories(
        {t.merchant_id for t in transactions if t.merchant_id is not None}
    )
    items = []
    for txn in transactions:
        item = TransactionResponse.from_row(txn)
        item.category = names.get(txn.category_id)
        item.merchant = payees.get(txn.merchant_id)
        item.payee_category = usual.get(txn.merchant_id)
        if icon := icons.get(item.merchant or txn.name):
            item.icon_url, item.icon_b64 = icon.url, icon.b64
        item.tags = [
            TagRef(id=t.id, name=t.name, color=t.color)
            for t in tags_by_txn.get(txn.id, [])
        ]
        item.splits = [split_line(s, names) for s in splits_by_txn.get(txn.id, [])]
        items.append(item)
    return items
