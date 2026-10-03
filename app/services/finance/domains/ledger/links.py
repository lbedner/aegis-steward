"""Transactions filed against places outside the ledger (#290, #303).

A charge belongs to a case ("Marisa's Root Canals") or a contact (the
dentist) the way a document does: by label - ``matter_tag``,
``party_tag`` - so one vocabulary says where anything is filed. Many to
many, and a link can be taken back off.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger import queries
from app.services.finance.models import FinanceTransaction, FinanceTransactionLink


async def link(db: AsyncSession, transaction_id: int, label: str) -> bool:
    """File the transaction under ``label``; False when it already is."""
    if await queries.transaction_link_id(db, transaction_id, label) is not None:
        return False
    db.add(FinanceTransactionLink(transaction_id=transaction_id, label=label))
    await db.flush()
    return True


async def unlink(db: AsyncSession, transaction_id: int, label: str) -> None:
    await queries.delete_transaction_link(db, transaction_id, label)
    await db.flush()


async def linked(
    db: AsyncSession, labels: Iterable[str]
) -> dict[str, list[FinanceTransaction]]:
    """Each label's live transactions, newest first, in one query."""
    wanted = list(dict.fromkeys(labels))
    found: dict[str, list[FinanceTransaction]] = {label: [] for label in wanted}
    if not wanted:
        return found
    for label, txn in await queries.transactions_by_label(db, wanted):
        found[label].append(txn)
    return found


async def filed(db: AsyncSession, labels: list[str]) -> dict[str, dict[str, Any]]:
    """Each label's transactions as her reads carry them: the rows a
    proposal can name, and their signed total - what a case cost, what
    was paid a contact. One query for every place listed."""
    from app.services.finance.domains.writes.display import candidate_row

    return {
        label: {
            "total_cents": sum(t.amount for t in txns),
            "items": [candidate_row(t) for t in txns],
        }
        for label, txns in (await linked(db, labels)).items()
    }
