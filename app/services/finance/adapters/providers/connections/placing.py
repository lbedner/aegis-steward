"""Where a newly linked account goes, when that is yours to say (#309).

A bank linked beside Quicken exports reports accounts the exports may
already feed. One whose last four (and bank, when the account names one)
match exactly one of yours attaches on its own (``upserts``). One that
could be several - or that has nothing to go on, as an export's account
seldom does - is held on its connection, its rows with it, until you
place it: onto one of yours, or as an account of its own. Then the
connection syncs from the start, so the history it held arrives and
pairs with the export's (``domains/ledger/two_feeds``).

Never a silent second account: that is the failure this exists to stop.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.adapters.providers import queries
from app.services.finance.constants import INVESTMENT_ACCOUNT_TYPES
from app.services.finance.domains.ledger import bank_link
from app.services.finance.models import FinanceAccount, FinanceConnection

if TYPE_CHECKING:
    from app.services.finance.adapters.providers.connections.upserts import (
        ProviderAccount,
    )

# The connection's ``metadata_`` keys: the accounts still to place, and
# the ones placed as accounts of their own (the next sync makes them).
UNPLACED = "unplaced"
OWN_ACCOUNTS = "own_accounts"
# The answer that is not one of yours.
OWN = "new"

# Each held account, with the accounts of yours it could be.
Choices = list[tuple[dict[str, Any], list[FinanceAccount]]]


def waiting(reported: ProviderAccount) -> dict[str, Any]:
    """A reported account as its connection holds it."""
    return {
        "id": reported.provider_account_id,
        "name": reported.name,
        "mask": reported.mask,
        "account_type": reported.account_type,
        "classification": reported.classification,
    }


def unplaced(connection: FinanceConnection) -> list[dict[str, Any]]:
    """The accounts this connection holds until you place them."""
    return list((connection.metadata_ or {}).get(UNPLACED, []))


def own_accounts(connection: FinanceConnection) -> set[str]:
    """The provider ids you placed as accounts of their own."""
    return set((connection.metadata_ or {}).get(OWN_ACCOUNTS, []))


def _remember(connection: FinanceConnection, key: str, value: list[Any]) -> None:
    kept = {k: v for k, v in (connection.metadata_ or {}).items() if k != key}
    if value:
        kept[key] = value
    if kept != (connection.metadata_ or {}):
        connection.metadata_ = kept


def hold(connection: FinanceConnection, accounts: list[dict[str, Any]]) -> None:
    """Hold ``accounts`` on the connection, or clear it when none are."""
    _remember(connection, UNPLACED, accounts)


def fits(held: dict[str, Any], accounts: list[FinanceAccount]) -> list[FinanceAccount]:
    """The accounts a held one could be: the same kind of money - assets or
    debts, cash or investments. Not the same account type: an export's is
    often a guess from its name."""
    investing = held["account_type"] in INVESTMENT_ACCOUNT_TYPES
    return [
        a
        for a in accounts
        if a.classification == held["classification"]
        and (a.account_type in INVESTMENT_ACCOUNT_TYPES) == investing
    ]


async def yours(
    db: AsyncSession, connection: FinanceConnection
) -> list[FinanceAccount]:
    """The accounts a newly linked one could already be: the ones a file
    export feeds, and the ones a disconnect kept (#307) - at this bank, or
    at one nobody knows. A kept account at another bank is that bank's."""
    return [
        a
        for a in await queries.unlinked_accounts(
            db, owner_user_id=connection.owner_user_id
        )
        if a.is_manual or a.institution_id in (None, connection.institution_id)
    ]


async def choices(db: AsyncSession, connection: FinanceConnection) -> Choices:
    """Each held account, with the accounts of yours it could be."""
    mine = await yours(db, connection)
    return [(held, fits(held, mine)) for held in unplaced(connection)]


async def place(
    db: AsyncSession,
    connection: FinanceConnection,
    answers: Mapping[str, str],
    offered: Choices | None = None,
) -> list[str]:
    """Put each held account where ``answers`` says (held id -> one of your
    account ids, or ``OWN``), then sync from the start next. Returns what
    is wrong with the answers, changing nothing; each of yours once.
    ``offered`` is ``choices``, when the caller has read them to show."""
    chosen: dict[str, FinanceAccount | None] = {}
    errors: list[str] = []
    if offered is None:
        offered = await choices(db, connection)
    for held, yours in offered:
        answer = answers.get(held["id"], "")
        by_id = {str(a.id): a for a in yours}
        if answer == OWN:
            chosen[held["id"]] = None
        elif answer in by_id and all(by_id[answer] is not a for a in chosen.values()):
            chosen[held["id"]] = by_id[answer]
        else:
            errors.append(f"Say where {held['name']} goes - each of yours once.")
    if errors or not chosen:
        return errors
    for held_id, account in chosen.items():
        if account is not None:
            # What the next sync finds it by; the sync fills in the rest.
            bank_link.link(
                account,
                provider=connection.provider,
                connection_id=connection.id,
                provider_account_id=held_id,
            )
            db.add(account)
    own = own_accounts(connection) | {i for i, a in chosen.items() if a is None}
    _remember(connection, OWN_ACCOUNTS, sorted(own))
    hold(connection, [])
    # Its rows were held with it: ask the provider for everything again.
    # What is already stored matches by id; only the held history is new.
    connection.sync_cursor = None
    db.add(connection)
    await db.flush()
    return []


async def sync_soon(connection: FinanceConnection) -> None:
    """Hand the connection's sync to the worker: after a place it pulls the
    whole history again, which is no request's job. Call it after the
    commit - the worker reads what was placed."""
    from app.components.worker.pools import get_queue_pool

    pool, queue_name = await get_queue_pool("system")
    await pool.enqueue_job(
        "finance_sync_connection_task",
        connection.id,
        connection.owner_user_id,
        _queue_name=queue_name,
    )
