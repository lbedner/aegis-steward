"""The brokerage accounts SnapTrade reports, mapped onto
``ProviderAccount``: the shared upsert keys them on SnapTrade's own id so
a renamed account stays one row."""

from typing import Any

from app.services.finance.adapters.providers.connections.common import _to_cents
from app.services.finance.adapters.providers.connections.upserts import (
    ProviderAccount,
)


def snaptrade_accounts(raw: list[dict[str, Any]]) -> list[ProviderAccount]:
    """SnapTrade's brokerage accounts (assets) in this app's terms; the
    account's ``balance.total`` is the provider-authoritative value."""
    accounts = []
    for account in raw:
        snaptrade_id = str(account.get("id") or "")
        if not snaptrade_id:
            continue
        number = account.get("number") or ""
        total = (account.get("balance") or {}).get("total") or {}
        accounts.append(
            ProviderAccount(
                provider_account_id=snaptrade_id,
                name=account.get("name")
                or account.get("institution_name")
                or "Brokerage",
                mask=number[-4:] if number else None,
                currency=(total.get("currency") or "usd").lower(),
                account_type="brokerage",
                classification="asset",
                current_balance=_to_cents(total.get("amount")),
            )
        )
    return accounts
