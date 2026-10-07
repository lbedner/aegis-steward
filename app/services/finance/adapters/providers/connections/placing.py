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

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
import re
from typing import TYPE_CHECKING, Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.adapters.providers import queries
from app.services.finance.constants import INVESTMENT_ACCOUNT_TYPES
from app.services.finance.domains.ledger import bank_link
from app.services.finance.models import FinanceAccount, FinanceConnection

if TYPE_CHECKING:
    from app.services.finance.adapters.providers.connections.upserts import (
        ProviderAccount,
        ProviderTransaction,
    )

# The connection's ``metadata_`` keys: the accounts still to place, and
# the ones placed as accounts of their own (the next sync makes them).
UNPLACED = "unplaced"
OWN_ACCOUNTS = "own_accounts"
# The answer that is not one of yours, and the one that leaves it held
# for another day.
OWN = "new"
SKIP = "skip"

# Each held account, with the accounts of yours it could be.
Choices = list[tuple[dict[str, Any], list[FinanceAccount]]]


def waiting(
    reported: ProviderAccount, institution_id: int | None = None
) -> dict[str, Any]:
    """A reported account as its connection holds it: enough to recognise
    it by - the bank (its row, for the logo), the balance - and its recent
    charges once a sync has read them (``remember_charges``)."""
    return {
        "id": reported.provider_account_id,
        "name": reported.name,
        "mask": reported.mask,
        "account_type": reported.account_type,
        "classification": reported.classification,
        "bank": reported.bank,
        "institution_id": institution_id,
        "balance": reported.current_balance,
    }


# How many of a held account's charges it keeps: enough to recognise it
# by, few enough to sit on its connection.
CHARGES_KEPT = 40


def remember_charges(
    connection: FinanceConnection, transactions: list[ProviderTransaction]
) -> None:
    """Each held account's most recent posted charges - ``[day, cents,
    name]`` - from a sync that did not store them: its history waits with
    it, and these say which of yours it is (``suggestions``)."""
    held = unplaced(connection)
    if not held:
        return
    by_account: dict[str, dict[str, ProviderTransaction]] = defaultdict(dict)
    for txn in transactions:
        # A first sync reads overlapping windows: one charge, once.
        if not txn.pending:
            by_account[txn.provider_account_id][txn.external_id] = txn
    for account in held:
        recent = sorted(
            by_account.get(account["id"], {}).values(), key=lambda t: t.date_
        )
        account["charges"] = [
            [t.date_.isoformat(), t.amount, (t.name or "")[:60]]
            for t in reversed(recent[-CHARGES_KEPT:])
        ]
    hold(connection, held)


def unplaced(connection: FinanceConnection) -> list[dict[str, Any]]:
    """The accounts this connection holds until you place them - copies: a
    change made to one in place would leave the stored JSON looking
    unchanged, and so unsaved."""
    return [dict(held) for held in (connection.metadata_ or {}).get(UNPLACED, [])]


def own_accounts(connection: FinanceConnection) -> set[str]:
    """The provider ids you placed as accounts of their own."""
    return set((connection.metadata_ or {}).get(OWN_ACCOUNTS, []))


def remember(connection: FinanceConnection, key: str, value: Any) -> None:
    kept = {k: v for k, v in (connection.metadata_ or {}).items() if k != key}
    if value:
        kept[key] = value
    if kept != (connection.metadata_ or {}):
        connection.metadata_ = kept


def hold(connection: FinanceConnection, accounts: list[dict[str, Any]]) -> None:
    """Hold ``accounts`` on the connection, or clear it when none are."""
    remember(connection, UNPLACED, accounts)


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


@dataclass(frozen=True)
class Suggestion:
    """Which of yours a held account is likely to be, and why."""

    account_id: int
    reason: str
    # The held account's charges it was found by, as indexes into them.
    matched: tuple[int, ...] = ()


# Charges in common that make an account the likely one, and words a
# name shares with every other one ("Account", "Card") that say nothing.
SHARED_CHARGES = 3
_FILLER = frozenset(
    {"account", "accounts", "card", "bank", "the", "of", "and", "primary", "my"}
)
# What a bank calls itself where your name for it is the short one.
_SAME_NAME = {"american express": "amex"}


def _words(*texts: str | None) -> set[str]:
    joined = " ".join(t or "" for t in texts).lower()
    for long, short in _SAME_NAME.items():
        joined = joined.replace(long, short)
    return {
        w
        for w in re.findall(r"[a-z0-9]+", joined)
        if len(w) > 1 and not w.isdigit() and w not in _FILLER
    }


async def suggestions(db: AsyncSession, offered: Choices) -> dict[str, Suggestion]:
    """The account of yours each held one is likely to be: the one sharing
    the most of its recent charges (same amount, dates as close as the
    pairing allows) - one-offs first, since a subscription could sit on any
    card you have - else the one whose name shares words with its own and
    its bank's. One read for all of them. Nothing where nothing says so,
    and never one of yours for two."""
    from app.services.finance.domains.ledger import two_feeds
    from app.services.finance.domains.ledger.queries.two_feeds import rows_between

    days = [
        date.fromisoformat(day)
        for held, _yours in offered
        for day, _cents, _name in held.get("charges") or []
    ]
    rows = []
    if days:
        window = timedelta(days=two_feeds.SAME_CHARGE_DAYS)
        rows = await rows_between(
            db,
            {a.id for _held, yours in offered for a in yours if a.id is not None},
            min(days) - window,
            max(days) + window,
        )
    by_id = {row.id: row for row in rows}
    picks: dict[str, tuple[tuple[int, int, int], Suggestion]] = {}
    for held, yours in offered:
        charges = held.get("charges") or []
        mine = _words(held["name"], held.get("bank"))
        scored = []
        for account in yours:
            theirs = two_feeds.Unpaired(r for r in rows if r.account_id == account.id)
            found = {
                i: row_id
                for i, (day, cents, _name) in enumerate(charges)
                if (row_id := theirs.take(account.id, date.fromisoformat(day), cents))
                is not None
            }
            one_offs = sum(
                1
                for row_id in found.values()
                if by_id[row_id].recurring_stream_id is None
            )
            named = len(mine & _words(account.name))
            scored.append(((one_offs, len(found), named), tuple(found), account))
        scored.sort(key=lambda pick: pick[0], reverse=True)
        if not scored:
            continue
        score, matched, best = scored[0]
        one_offs, shared, named = score
        runner_up = scored[1][0] if len(scored) > 1 else (0, 0, 0)
        if shared >= SHARED_CHARGES and score[:2] > runner_up[:2]:
            reason = f"{shared} of {len(charges)} recent charges match"
            if repeating := shared - one_offs:
                reason += f", {repeating} of them repeating"
        elif not shared and named and score > runner_up:
            reason, matched = "by name", ()
        else:
            continue
        assert best.id is not None
        picks[held["id"]] = (score, Suggestion(best.id, reason, matched))
    # One of yours for one of theirs: where two point at the same account,
    # the stronger keeps it.
    strongest: dict[int, tuple[tuple[int, int, int], str]] = {}
    for held_id, (score, pick) in picks.items():
        if pick.account_id not in strongest or score > strongest[pick.account_id][0]:
            strongest[pick.account_id] = (score, held_id)
    return {
        held_id: pick
        for held_id, (_score, pick) in picks.items()
        if strongest[pick.account_id][1] == held_id
    }


async def place(
    db: AsyncSession,
    connection: FinanceConnection,
    answers: Mapping[str, str],
    offered: Choices | None = None,
) -> list[str]:
    """Put each held account where ``answers`` says (held id -> one of your
    account ids, or ``OWN``; ``SKIP`` leaves it held), then sync from the
    start next. Returns what
    is wrong with the answers, changing nothing; each of yours once.
    ``offered`` is ``choices``, when the caller has read them to show."""
    chosen: dict[str, FinanceAccount | None] = {}
    banks: dict[str, int | None] = {}
    skipped: list[dict[str, Any]] = []
    errors: list[str] = []
    if offered is None:
        offered = await choices(db, connection)
    for held, yours in offered:
        answer = answers.get(held["id"], "")
        by_id = {str(a.id): a for a in yours}
        if answer == SKIP:
            skipped.append(held)
        elif answer == OWN:
            chosen[held["id"]] = None
        elif answer in by_id and all(by_id[answer] is not a for a in chosen.values()):
            chosen[held["id"]] = by_id[answer]
            banks[held["id"]] = held.get("institution_id")
        else:
            errors.append(f"Say where {held['name']} goes - each of yours once.")
    if errors:
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
            # The bank says which bank it is at, where nobody had (#410).
            account.institution_id = account.institution_id or banks.get(held_id)
            db.add(account)
    own = own_accounts(connection) | {i for i, a in chosen.items() if a is None}
    remember(connection, OWN_ACCOUNTS, sorted(own))
    hold(connection, skipped)
    # Its rows were held with it: ask the provider for everything again.
    # What is already stored matches by id; only the held history is new.
    if chosen:
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
