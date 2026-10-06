"""One account where there were two (#309).

A bank linked before it could attach to the account a Quicken export
already fed made a copy beside it: the same checking account twice, its
balance counted twice, every charge twice. Merging folds the bank's copy
into your account: the copy's rows move over (everything pointing at it -
``queries.purge.ACCOUNT_COLUMNS``), its bank link with them so the next
sync lands on your account, and a charge both feeds brought is paired
once (``two_feeds``). Your account keeps its name and what you set on it;
where both hold a one-per-account row (a day's balance, the loan terms, a
detected stream), yours stays.

Only that case: the copy a bank's, the one kept yours. Two copies of one
feed hold the same provider ids, and two file-fed accounts would leave
moved rows hashed for the account they left - a later import of the same
file would not know them.

Previewed first (``plan``): nothing about a merge can be undone.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.time import utcnow
from app.services.finance.domains.ledger import bank_link, two_feeds
from app.services.finance.domains.ledger.queries.accounts import (
    live_transaction_count,
)
from app.services.finance.domains.ledger.queries.networth import (
    live_accounts_for_owner,
)
from app.services.finance.domains.ledger.queries.purge import repoint_account
from app.services.finance.models import FinanceAccount


@dataclass(frozen=True)
class Plan:
    """What merging ``gone`` into ``stays`` would do, or did."""

    moving: int = 0  # rows moving over, every feed's
    same_charges: int = 0  # charges both feeds brought, kept once
    link_moves: bool = False  # the copy's bank link comes along
    refusal: str | None = None


def refusal(stays: FinanceAccount, gone: FinanceAccount) -> str | None:
    """Why ``gone`` cannot fold into ``stays``, or None."""
    if stays.id == gone.id:
        return "An account is already itself."
    if stays.classification != gone.classification:
        return "Money you hold and money you owe cannot be one account."
    if not stays.is_manual:
        return (
            "This one is a bank's. Open the account your files feed and "
            "merge this one into it."
        )
    if gone.is_manual:
        return "Pick the copy a bank link made; an account of yours stays."
    return None


async def candidates(db: AsyncSession, stays: FinanceAccount) -> list[FinanceAccount]:
    """The accounts that could fold into ``stays`` - hidden ones too: a
    duplicate is the first thing somebody hides."""
    return [
        a
        for a in await live_accounts_for_owner(db, owner_user_id=stays.owner_user_id)
        if refusal(stays, a) is None
    ]


async def plan(db: AsyncSession, stays: FinanceAccount, gone: FinanceAccount) -> Plan:
    """The preview: what a merge would move and pair, or why it cannot."""
    if why := refusal(stays, gone):
        return Plan(refusal=why)
    assert stays.id is not None and gone.id is not None
    pairs = await two_feeds.same_charges(db, [stays.id, gone.id], as_one=stays.id)
    return Plan(
        moving=await live_transaction_count(db, gone.id),
        same_charges=len(pairs),
        link_moves=gone.connection_id is not None,
    )


async def merge(db: AsyncSession, stays: FinanceAccount, gone: FinanceAccount) -> Plan:
    """Fold ``gone`` into ``stays``: what it did, counted as it happened,
    or the refusal with nothing done. Writes but does not commit."""
    if why := refusal(stays, gone):
        return Plan(refusal=why)
    assert stays.id is not None and gone.id is not None
    from app.services.finance.domains.ledger import networth
    from app.services.finance.domains.writes import queue

    # A proposal aimed at the copy could rename or reassign the wrong
    # account if it followed the rows here: set aside, saying why.
    await queue.expire_naming(
        db,
        [gone.id],
        owner_user_id=stays.owner_user_id,
        note=f"Its account was merged into {stays.name}; ask again there.",
    )
    moving = await live_transaction_count(db, gone.id)
    link_moves = gone.connection_id is not None
    ids = (gone.provider, gone.connection_id, gone.provider_account_id)
    persistent = gone.persistent_account_id
    # Off the copy first: one connection names one provider account. The
    # next sync rewrites the rest - name aside - from the bank.
    bank_link.unlink(gone, forget=True)
    db.add(gone)
    await db.flush()
    provider, connection_id, provider_account_id = ids
    bank_link.link(
        stays,
        provider=provider,
        connection_id=connection_id,
        provider_account_id=provider_account_id,
        persistent_account_id=persistent,
    )
    db.add(stays)
    await repoint_account(db, gone.id, stays.id)
    pairs = await two_feeds.same_charges(db, [stays.id], as_one=stays.id)
    await two_feeds.pair_ids(db, pairs)
    gone.deleted_at = utcnow()
    db.add(gone)
    await db.flush()
    # The day-by-day worth counted both copies; it counts one now.
    await networth.recompute_snapshots(db, owner_user_id=stays.owner_user_id)
    return Plan(moving=moving, same_charges=len(pairs), link_moves=link_moves)
