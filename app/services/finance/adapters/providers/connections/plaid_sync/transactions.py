"""Transactions: the cursor-based ``transactions/sync`` lane.

Plaid's rows mapped onto ``ProviderTransaction`` (the shared upsert
dedups them on Plaid's ``transaction_id``), and the removal half - Plaid
reports deletions explicitly, so a charge that vanishes upstream has to
vanish here rather than linger as a ghost.
"""

from __future__ import annotations

from datetime import date
import logging
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.time import utcnow
from app.services.finance.adapters.providers import queries
from app.services.finance.adapters.providers.connections import plaid_mapping
from app.services.finance.adapters.providers.connections.upserts import (
    ProviderTransaction,
)
from app.services.finance.constants import Provider
from app.services.finance.models import FinanceTransaction

logger = logging.getLogger(__name__)


def plaid_transactions(raw: list[dict[str, Any]]) -> list[ProviderTransaction]:
    """Plaid's added/modified transactions in this app's terms. Plaid counts
    an outflow positive, so the amount is negated."""
    return [
        ProviderTransaction(
            provider_account_id=txn.get("account_id") or "",
            external_id=txn["transaction_id"],
            amount=-round(txn["amount"] * 100) if txn.get("amount") is not None else 0,
            date_=date.fromisoformat(txn["date"]),
            name=txn.get("merchant_name") or txn.get("name"),
            currency=(txn.get("iso_currency_code") or "usd").lower(),
            original_description=txn.get("name"),
            pending=bool(txn.get("pending")),
            pending_provider_id=txn.get("pending_transaction_id"),
            category_primary=(txn.get("personal_finance_category") or {}).get(
                "primary"
            ),
            logo_url=plaid_mapping.merchant_logo(txn),
        )
        for txn in raw
    ]


async def _remove_transactions(
    db: AsyncSession,
    removed: list[dict[str, Any]],
    account_by_plaid_id: dict[str, int],
) -> int:
    """Tombstone retracted transactions (phantom pre-auths, bank deletions).

    Soft-delete only — the tombstone (``is_removed``/``removed_at``/``status``)
    must survive re-syncs so a replayed page can't resurrect the row. Scoped to
    the account the removed[] entry names when Plaid provides it.
    """
    count = 0
    now = utcnow()
    for item in removed:
        conditions = [
            FinanceTransaction.source == Provider.PLAID,
            FinanceTransaction.external_id == item["transaction_id"],
            FinanceTransaction.deleted_at.is_(None),
        ]
        plaid_account_id = item.get("account_id")
        account_id = (
            account_by_plaid_id.get(plaid_account_id) if plaid_account_id else None
        )
        if account_id is not None:
            conditions.append(FinanceTransaction.account_id == account_id)
        txn = await queries.transaction_first_where(db, conditions)
        if txn is not None:
            txn.is_removed = True
            txn.removed_at = now
            txn.status = "removed"
            txn.deleted_at = now
            db.add(txn)
            count += 1
    return count
