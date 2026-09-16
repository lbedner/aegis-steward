"""How an account reads on the portfolio: its balance, its group, its
statement line. Pure shaping; the routes in ``accounts.py`` draw it."""

from __future__ import annotations

from typing import Any

from app.components.web_frontend.filters import money
from app.services.finance.constants import account_sections
from app.services.finance.domains.ledger.accounts import effective_balance
from app.services.finance.schemas import AccountResponse


def balance(account: AccountResponse) -> int:
    return effective_balance(
        current_balance=account.current_balance,
        balance_as_of=account.balance_as_of,
        classification=account.classification,
        activity_balance=account.activity_balance,
    )


def grouped(accounts: list[AccountResponse]) -> list[dict[str, Any]]:
    """Ledger-order groups, each with a subtotal, largest balance first."""
    groups = []
    for label, members in account_sections(accounts):
        rows = sorted(members, key=balance, reverse=True)
        groups.append(
            {
                "label": label,
                "subtotal": sum(balance(a) for a in rows),
                "accounts": [{"account": a, "balance": balance(a)} for a in rows],
            }
        )
    return groups


def statement_line(account: AccountResponse) -> str | None:
    """``Due Jul 15 · min $35.00`` under a credit account, when reported."""
    liability = account.liability
    if liability is None:
        return None
    parts: list[str] = []
    if liability.next_payment_due_date:
        due = liability.next_payment_due_date
        parts.append(f"Due {due:%b} {due.day}")
    if liability.minimum_payment_amount is not None:
        parts.append(f"min {money(liability.minimum_payment_amount, account.currency)}")
    return " · ".join(parts) or None
