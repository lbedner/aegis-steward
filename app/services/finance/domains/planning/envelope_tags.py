"""An envelope that pays for what wears a tag.

Vanessa's allowance is virtual, but what the household buys her is
real: Roblox codes on the family card. Tag the charge "Vanessa" and her
envelope pays for it; untag it and the money comes back (#240).

The envelope SETTLES against the ledger rather than being walked on
each tag: it remembers how much tagged spend it has counted and walks
only the difference. So a re-import that drops and re-adds a tag, a
charge edited or deleted, or a settle run twice, all come out right,
and every reader of the balance stays correct without knowing tags exist.
"""

from __future__ import annotations

from datetime import date

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.detection.insights.formatting import format_usd
from app.services.finance.domains.planning import envelopes, queries
from app.services.finance.models import FinanceAccount


async def follow_tag(
    db: AsyncSession,
    account_id: int,
    name: str | None,
    *,
    owner_user_id: int | None,
    since: date,
) -> FinanceAccount:
    """Point an envelope at a tag, counting from ``since``; None stops it.

    Counting starts fresh: the real "Vanessa" tag has charges on it back
    to 2019, and an envelope pointed at it today pays for what comes
    next, not for all of those.
    """
    from app.services.finance.domains.ledger.transactions import get_or_create_tag

    account = await envelopes.accounts.get_account(
        db, account_id, owner_user_id=owner_user_id
    )
    if account is None or envelopes.envelope_metadata(account.metadata_) is None:
        raise ValueError(f"No envelope {account_id}.")
    metadata = {
        k: v
        for k, v in (account.metadata_ or {}).items()
        if k not in envelopes._TAG_KEYS
    }
    if name and name.strip():
        tag = await get_or_create_tag(db, name.strip(), owner_user_id=owner_user_id)
        metadata |= {
            envelopes.TAG_KEY: tag.id,
            envelopes.TAG_SINCE_KEY: since.isoformat(),
            envelopes.TAG_COUNTED_KEY: 0,
        }
    account.metadata_ = metadata
    db.add(account)
    await db.flush()
    return account


async def tag_name(db: AsyncSession, meta: envelopes.EnvelopeMeta | None) -> str | None:
    """The name of the tag an envelope pays for, or None. Read, never
    stored beside the id: a renamed tag must not leave a stale copy."""
    from app.services.finance.models import FinanceTag

    if meta is None or meta.tag_id is None:
        return None
    tag = await db.get(FinanceTag, meta.tag_id)
    return tag.name if tag is not None else None


async def tag_names(db: AsyncSession, accounts: list[FinanceAccount]) -> dict[int, str]:
    """Each tag-following envelope's tag name, by account id - one query
    for the lot, not one per envelope."""
    from sqlmodel import col, select

    from app.services.finance.models import FinanceTag

    following = {
        account.id: meta.tag_id
        for account in accounts
        if (meta := envelopes.envelope_metadata(account.metadata_)) is not None
        and meta.tag_id is not None
    }
    if not following:
        return {}
    rows = await db.exec(
        select(FinanceTag).where(col(FinanceTag.id).in_(following.values()))
    )
    names = {tag.id: tag.name for tag in rows.all()}
    return {
        account_id: names[tag_id]
        for account_id, tag_id in following.items()
        if tag_id in names
    }


async def retag(
    db: AsyncSession,
    account_id: int,
    name: str,
    *,
    owner_user_id: int | None,
    since: date | None = None,
) -> bool:
    """Follow ``name`` ("" for none) from ``since``, then settle. True when
    anything changed.

    ``since`` None leaves the start where it is - re-saving the dialog
    must not move it - and a NEW tag starts today unless told otherwise.
    The same tag with a new start keeps what it has counted, and settle
    walks only the difference: moving the start back spends the tagged
    charges it now covers, moving it forward gives them back. Two August
    Dark Side Records charges tagged after the fact are why (2026-09-23).
    """
    from app.services.finance.utils import current_date

    account = await envelopes.accounts.get_account(
        db, account_id, owner_user_id=owner_user_id
    )
    meta = envelopes.envelope_metadata(account.metadata_) if account else None
    if account is None or meta is None:
        raise ValueError(f"No envelope {account_id}.")
    wanted = name.strip() or None
    if wanted != await tag_name(db, meta):
        start = since or current_date()
        await follow_tag(
            db, account_id, wanted, owner_user_id=owner_user_id, since=start
        )
    elif wanted is None or since is None or since == meta.tag_since:
        return False
    else:
        account.metadata_ = {
            **(account.metadata_ or {}),
            envelopes.TAG_SINCE_KEY: since.isoformat(),
        }
        db.add(account)
        await db.flush()
    await settle(db, owner_user_id=owner_user_id)
    return True


async def settle(db: AsyncSession, *, owner_user_id: int | None) -> int:
    """Walk every tag-following envelope by the tagged spend it has not
    counted yet. Returns how many envelopes moved; safe to run any time."""
    moved = 0
    for account in await envelopes.list_envelopes(db, owner_user_id=owner_user_id):
        meta = envelopes.envelope_metadata(account.metadata_)
        if meta is None or meta.tag_id is None or meta.tag_since is None:
            continue
        spent = await queries.tagged_spend(db, tag_id=meta.tag_id, since=meta.tag_since)
        change = spent - meta.tag_counted
        if not change:
            continue
        refreshed = await envelopes.walk_envelope(
            db,
            account.id,
            delta=-change,
            owner_user_id=account.owner_user_id,
            when=None,
            note=f"Tagged: {format_usd(abs(change))} "
            + ("spent" if change > 0 else "given back"),
        )
        refreshed.metadata_ = {
            **(refreshed.metadata_ or {}),
            envelopes.TAG_COUNTED_KEY: spent,
        }
        db.add(refreshed)
        moved += 1
    await db.flush()
    return moved
