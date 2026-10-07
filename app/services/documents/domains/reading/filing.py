"""Paper that arrived before the household knew what it was about (#409).

The reader files a document only by what is on file - an account's last
four, a contact's website or phone, a bank's routing number - and a
document that lands first sits unfiled with nothing trying again. The M1
statement did: the account had no last four, the bank no contact.

So the unfiled pile is read again when a fact lands (an approved card
that carries one, a number typed into Manage, a sync that brought a
bank), and nightly as the net. Only the identity pass over text already
extracted: no page is read again and no model is called, which is what
makes it cheap enough to run inline where the fact lands.
"""

from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import get_async_session
from app.core.log import logger

# The cards whose approval can land a fact the reader files by.
FACT_CHANGES = frozenset(
    {
        "document.metadata",  # a last four read off the paper
        "contact.create",
        "contact.amend",
        "account.create",
        "account.institution",
    }
)


async def reread_unfiled(db: AsyncSession, *, owner_user_id: int | None) -> int:
    """Propose a filing for every live document filed under nobody and
    nothing, where the front now matches something on file. Returns how
    many cards were made; a document already asked about makes none."""
    from app.services.documents.domains.reading.identity import known_strings
    from app.services.documents.domains.reading.proposals import _propose_metadata
    from app.services.documents.queries import unfiled_documents

    waiting = await unfiled_documents(db, owner_user_id=owner_user_id)
    if not waiting:
        return 0
    known = await known_strings(db)
    # ponytail: a reading per document; the pile is a handful. Batch the
    # per-document reads (pages, letterhead, cards waiting) if it grows.
    made = 0
    for document_id in waiting:
        if await _propose_metadata(db, document_id, owner_user_id, known=known):
            made += 1
    return made


async def reread_unfiled_job() -> None:
    """The nightly net, before the day's arrivals are joined: the unfiled
    pile read again, and every waiting receipt looked for again - which
    is also when one that waited too long asks for a person (#330).
    Owns its session: a scheduler fires with no request behind it."""
    from app.services.documents.domains.reading import receipts

    async with get_async_session() as db:
        made = await reread_unfiled(db, owner_user_id=None)
        await receipts.match_waiting(db, owner_user_id=None)
        await db.commit()
    logger.info("Unfiled documents read again: %d proposed", made)
