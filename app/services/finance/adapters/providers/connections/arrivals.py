"""A new account at a linked bank appears and says so (#313).

A savings account opened inside a bank already linked never showed up
until somebody re-ran the bank's own sign-in (r/MonarchMoney, 2026-10-01),
and an account a sync did make arrived silently. Two halves:

- Plaid says so first (``NEW_ACCOUNTS_AVAILABLE``): the connection keeps
  the notice, the banner offers Reconnect, and Reconnect opens Plaid's
  page with account selection on. Done clears it.
- A sync that makes an account at a bank already synced names it in
  Attention, once. Never the first sync (everything is new then) and
  never one you placed as an account of its own (you said so).
"""

from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.adapters.providers.connections import placing
from app.services.finance.constants import NEW_ACCOUNT_INSIGHT_TYPE
from app.services.finance.models import FinanceAccount, FinanceConnection
from app.services.finance.utils import stored_owner

# The connection's ``metadata_`` key, and what the banner says for it.
NEW_ACCOUNTS = "new_accounts"
WORDS = "New accounts to add"


def waiting_to_add(connection: FinanceConnection) -> bool:
    """Whether the bank has said it holds accounts nobody added yet."""
    return bool((connection.metadata_ or {}).get(NEW_ACCOUNTS))


def offer(connection: FinanceConnection, on: bool) -> None:
    """Keep the bank's notice, or clear it once Reconnect is done."""
    placing.remember(connection, NEW_ACCOUNTS, on)


async def announce(
    db: AsyncSession, connection: FinanceConnection, made: list[FinanceAccount]
) -> None:
    """Name in Attention each account this sync made at a bank already
    synced. Deduped by account, so a re-sync never says it twice."""
    from app.services.finance.domains.detection.insights.rules.shared import (
        create_insight_if_new,
    )

    if connection.last_successful_sync_at is None:
        return
    own = placing.own_accounts(connection)
    bank = connection.label or "your bank"
    for account in made:
        if account.id is None or account.provider_account_id in own:
            continue
        await create_insight_if_new(
            db,
            owner_user_id=stored_owner(connection.owner_user_id),
            insight_type=NEW_ACCOUNT_INSIGHT_TYPE,
            dedup_key=f"{NEW_ACCOUNT_INSIGHT_TYPE}:{account.id}",
            severity="info",
            title=f"New account at {bank}: {account.name}",
            body="It arrived with the last sync and is counted from now on. "
            "Hide it from the account's Manage menu if it is not yours to track.",
            related_account_id=account.id,
        )
