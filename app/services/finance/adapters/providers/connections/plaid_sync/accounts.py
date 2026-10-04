"""Accounts and liabilities: what Plaid says the account IS.

Plaid's accounts mapped onto ``ProviderAccount`` (the shared upsert keeps
a renamed or re-linked account one row), and the liability detail that
carries the APRs and due dates the credit rules later read.
"""

from __future__ import annotations

from datetime import date
import logging
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.time import utcnow
from app.services.finance.adapters.providers.connections.common import _to_cents
from app.services.finance.adapters.providers.connections.upserts import (
    ProviderAccount,
)
from app.services.finance.domains.ledger import queries as ledger_queries
from app.services.finance.models import (
    FinanceLiabilityDetail,
)

logger = logging.getLogger(__name__)


_PLAID_TYPE_MAP: dict[str, tuple[str, str]] = {
    "depository": ("checking", "asset"),
    "credit": ("credit_card", "liability"),
    "loan": ("loan", "liability"),
    "investment": ("brokerage", "asset"),
}
_DEPOSITORY_SAVINGS = frozenset({"savings", "cd", "money market", "hsa"})


def _map_account_kind(
    plaid_type: str | None, plaid_subtype: str | None
) -> tuple[str, str]:
    account_type, classification = _PLAID_TYPE_MAP.get(
        plaid_type or "", ("other_asset", "asset")
    )
    if plaid_type == "depository" and (plaid_subtype or "") in _DEPOSITORY_SAVINGS:
        account_type = "savings"
    return account_type, classification


def plaid_accounts(raw: list[dict[str, Any]]) -> list[ProviderAccount]:
    """Plaid's accounts in this app's terms. ``persistent_account_id`` is
    what real institutions keep across a re-link; the sandbox has none,
    and the shared upsert falls back to name + mask."""
    accounts = []
    for plaid_account in raw:
        balances = plaid_account.get("balances") or {}
        account_type, classification = _map_account_kind(
            plaid_account.get("type"), plaid_account.get("subtype")
        )
        accounts.append(
            ProviderAccount(
                provider_account_id=plaid_account["account_id"],
                persistent_account_id=plaid_account.get("persistent_account_id"),
                name=plaid_account.get("name")
                or plaid_account.get("official_name")
                or "Account",
                mask=plaid_account.get("mask"),
                currency=(balances.get("iso_currency_code") or "usd").lower(),
                account_type=account_type,
                classification=classification,
                current_balance=_to_cents(balances.get("current")),
                available_balance=_to_cents(balances.get("available")),
            )
        )
    return accounts


def _pct_to_bps(pct: float | None) -> int | None:
    return int(round(pct * 100)) if pct is not None else None


async def _apply_liabilities(
    db: AsyncSession,
    liabilities: dict[str, Any],
    account_by_plaid_id: dict[str, int],
    *,
    owner_user_id: int | None,
) -> int:
    """Upsert credit-lane liability detail, 1:1 per account, ever.

    Money lands as int cents, APRs as basis points (int) — no floats stored.
    Fields the institution doesn't report (the AMEX case) stay NULL.
    """
    entries = liabilities.get("credit") or []
    if not entries:
        return 0
    touched = [
        account_by_plaid_id[e["account_id"]]
        for e in entries
        if e.get("account_id") in account_by_plaid_id
    ]
    existing_by_account = await ledger_queries.liability_details_by_account(db, touched)
    written = 0
    for entry in entries:
        account_id = account_by_plaid_id.get(entry.get("account_id"))
        if account_id is None:
            continue
        detail = existing_by_account.get(account_id)
        if detail is None:
            detail = FinanceLiabilityDetail(
                owner_user_id=owner_user_id, account_id=account_id
            )
        detail.liability_type = "credit"
        detail.last_statement_balance = _to_cents(entry.get("last_statement_balance"))
        raw_issue = entry.get("last_statement_issue_date")
        detail.last_statement_issue_date = (
            date.fromisoformat(raw_issue) if raw_issue else None
        )
        detail.last_payment_amount = _to_cents(entry.get("last_payment_amount"))
        raw_paid = entry.get("last_payment_date")
        detail.last_payment_date = date.fromisoformat(raw_paid) if raw_paid else None
        detail.minimum_payment_amount = _to_cents(entry.get("minimum_payment_amount"))
        raw_due = entry.get("next_payment_due_date")
        detail.next_payment_due_date = date.fromisoformat(raw_due) if raw_due else None
        detail.is_overdue = entry.get("is_overdue")
        detail.aprs = [
            {
                "apr_type": apr.get("apr_type"),
                "apr_percentage_bps": _pct_to_bps(apr.get("apr_percentage")),
                "balance_subject_to_apr": _to_cents(apr.get("balance_subject_to_apr")),
                "interest_charge_amount": _to_cents(apr.get("interest_charge_amount")),
            }
            for apr in entry.get("aprs") or []
        ]
        detail.raw = entry
        detail.updated_at = utcnow()
        db.add(detail)
        written += 1
    await db.flush()
    return written
