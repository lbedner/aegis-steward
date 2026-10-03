"""SimpleFIN's accounts and transactions in this app's terms.

The Bridge reports amounts as decimal strings (negative for money out,
this app's convention already) and dates as Unix seconds. It says nothing
about what kind of account something is, so the kind is read from the
name and the balance; the user corrects one it gets wrong.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.services.finance.adapters.providers.connections.upserts import (
    ProviderAccount,
    ProviderTransaction,
)

# ponytail: kind from words in the account's name, then the balance's
# sign; SimpleFIN has no account type. A per-institution mapping if these
# words miss.
_KIND_WORDS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("credit", "card", "visa", "mastercard", "amex"), "credit_card", "liability"),
    (("mortgage", "loan", "heloc"), "loan", "liability"),
    (("saving", "money market", "cd "), "savings", "asset"),
    (("brokerage", "ira", "401k", "invest"), "brokerage", "asset"),
)


def _cents(amount: Any) -> int | None:
    try:
        return round(Decimal(str(amount)) * 100) if amount is not None else None
    except InvalidOperation:
        return None


def _day(seconds: Any) -> date:
    return datetime.fromtimestamp(int(seconds), UTC).date()


def _currency(code: Any) -> str:
    """ISO codes as this app stores them; the protocol also allows a URL
    for a custom currency, which this app has no use for."""
    code = str(code or "usd")
    return code.lower() if len(code) == 3 else "usd"


def _kind(name: str, balance: int | None) -> tuple[str, str]:
    lowered = f"{name.lower()} "
    for words, account_type, classification in _KIND_WORDS:
        if any(word in lowered for word in words):
            return account_type, classification
    if balance is not None and balance < 0:
        return "credit_card", "liability"
    return "checking", "asset"


def simplefin_accounts(payload: dict[str, Any]) -> list[ProviderAccount]:
    accounts = []
    for account in payload.get("accounts") or []:
        if not account.get("id"):
            continue
        name = account.get("name") or "Account"
        balance = _cents(account.get("balance"))
        account_type, classification = _kind(name, balance)
        accounts.append(
            ProviderAccount(
                provider_account_id=str(account["id"]),
                name=name,
                mask=None,
                currency=_currency(account.get("currency")),
                account_type=account_type,
                classification=classification,
                current_balance=balance,
                available_balance=_cents(account.get("available-balance")),
            )
        )
    return accounts


def simplefin_transactions(payload: dict[str, Any]) -> list[ProviderTransaction]:
    """A pending row has ``posted`` 0: it is dated by when it was made."""
    transactions = []
    for account in payload.get("accounts") or []:
        currency = _currency(account.get("currency"))
        for txn in account.get("transactions") or []:
            amount = _cents(txn.get("amount"))
            when = txn.get("posted") or txn.get("transacted_at")
            if not txn.get("id") or amount is None or not when:
                continue
            description = txn.get("description")
            transactions.append(
                ProviderTransaction(
                    provider_account_id=str(account.get("id")),
                    external_id=str(txn["id"]),
                    amount=amount,
                    date_=_day(when),
                    name=txn.get("payee") or description,
                    currency=currency,
                    original_description=txn.get("memo") or description,
                    pending=bool(txn.get("pending")),
                )
            )
    return transactions


def bridge_errors(payload: dict[str, Any]) -> list[str]:
    """What the Bridge says is wrong (``errlist``), as the user should read it."""
    return [
        str(error.get("msg") or error.get("code"))
        if isinstance(error, dict)
        else str(error)
        for error in payload.get("errlist") or []
    ]


def bank_names(payload: dict[str, Any]) -> str | None:
    """The banks behind the access, named the way the Bridge names them."""
    names = [c["name"] for c in payload.get("connections") or [] if c.get("name")]
    return ", ".join(names) or None
