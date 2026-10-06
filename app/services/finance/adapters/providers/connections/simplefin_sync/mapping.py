"""SimpleFIN's accounts and transactions in this app's terms.

The Bridge reports amounts as decimal strings (negative for money out,
this app's convention already) and dates as Unix seconds. It says nothing
about what kind of account something is, so the kind is guessed from the
name the way a file import guesses it, then from a negative balance; the
user corrects one it gets wrong.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
import re
from typing import Any

from app.services.finance.adapters.importers.base import infer_account_kind
from app.services.finance.adapters.providers.connections.common import _to_cents
from app.services.finance.adapters.providers.connections.upserts import (
    ProviderAccount,
    ProviderTransaction,
)
from app.services.finance.domains.ledger.merchant_icon import domain_from_website


def _day(seconds: Any) -> date:
    return datetime.fromtimestamp(int(seconds), UTC).date()


def _currency(code: Any) -> str:
    """ISO codes as this app stores them; the protocol also allows a URL
    for a custom currency, which this app has no use for."""
    code = str(code or "usd")
    return code.lower() if len(code) == 3 else "usd"


def _kind(name: str, balance: int | None) -> tuple[str, str]:
    """The import's guess from the name; a name it cannot place but owing
    money is a card. Anything else stays a generic asset to reclassify."""
    kind = infer_account_kind(name)
    if kind == ("other_asset", "asset") and balance is not None and balance < 0:
        return "credit_card", "liability"
    return kind


# SimpleFIN carries an account's last four in its name, "... (3639)".
_LAST_FOUR = re.compile(r"\((\d{4})\)\s*$")


def last_four(name: str) -> str | None:
    """The last four a SimpleFIN account's name ends with, if it does."""
    found = _LAST_FOUR.search(name)
    return found.group(1) if found else None


def simplefin_accounts(payload: dict[str, Any]) -> list[ProviderAccount]:
    banks = {c.get("conn_id"): c for c in payload.get("connections") or []}
    accounts = []
    for account in payload.get("accounts") or []:
        if not account.get("id"):
            continue
        name = account.get("name") or "Account"
        bank = banks.get(account.get("conn_id")) or {}
        balance = _to_cents(account.get("balance"))
        account_type, classification = _kind(name, balance)
        accounts.append(
            ProviderAccount(
                provider_account_id=str(account["id"]),
                name=name,
                mask=last_four(name),
                bank=bank.get("name") or bank.get("org_name"),
                bank_domain=domain_from_website(bank.get("org_url")),
                currency=_currency(account.get("currency")),
                account_type=account_type,
                classification=classification,
                current_balance=balance,
                available_balance=_to_cents(account.get("available-balance")),
            )
        )
    return accounts


def simplefin_transactions(payload: dict[str, Any]) -> list[ProviderTransaction]:
    """A pending row has ``posted`` 0: it is dated by when it was made."""
    transactions = []
    for account in payload.get("accounts") or []:
        currency = _currency(account.get("currency"))
        for txn in account.get("transactions") or []:
            amount = _to_cents(txn.get("amount"))
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


# Codes "meant for the developer and not the user" (the protocol): how
# this app calls the API. Logged, never shown as the user's problem.
_FOR_THE_DEVELOPER = "gen.api"


def bridge_errors(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    """What the Bridge says is wrong (``errlist``): the messages for the
    user (a bank to log in to again, an account it could not read), and
    those for the developer."""
    for_user: list[str] = []
    for_developer: list[str] = []
    for error in payload.get("errlist") or []:
        found = error if isinstance(error, dict) else {"msg": str(error)}
        message = str(found.get("msg") or found.get("code") or "")
        code = str(found.get("code") or "")
        (for_developer if code.startswith(_FOR_THE_DEVELOPER) else for_user).append(
            message
        )
    return for_user, for_developer


def bank_names(payload: dict[str, Any]) -> str | None:
    """The banks behind the access, named the way the Bridge names them."""
    names = [c["name"] for c in payload.get("connections") or [] if c.get("name")]
    return ", ".join(names) or None
