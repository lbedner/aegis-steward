"""Finding a document without knowing its number.

``paper(document_id)`` needs an id you already have, and ``parties()``
only reports documents that are ALREADY tagged to somebody. So the
documents most in need of attention - the ones nobody has attributed,
whose sender is the thing missing - were the only ones an agent could
not see.

That surfaced the day a letter carrying Dutchess County DSS's own
street address sat on the shelf attached to nobody, and the only way to
read it was for a person to look its id out of the database by hand
(2026-09-18). A shelf you can only address by number is a shelf whose
unfiled corner is invisible, which is exactly backwards.

Read-only. Nothing here writes; ``document.metadata`` is the door.
"""

from __future__ import annotations

from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

# Enough to choose from, short enough that a listing is still readable
# in a turn. A search that needs more than this wants a better query.
SHELF_PAGE = 40


async def shelf(
    db: AsyncSession,
    *,
    q: str | None = None,
    kind: str | None = None,
    unattributed: bool = False,
    owner_user_id: int | None = None,
) -> list[dict[str, Any]]:
    """What is on the shelf, newest first.

    ``q`` matches the title, case-insensitively, because that is what a
    person has to go on: "the county letter", "the Delta invoice".

    ``unattributed`` narrows to documents no party is tagged on. That is
    the working queue for reading letterheads: a document with a sender
    has already been placed, and one without is where the next
    ``document.metadata`` should go.

    Each row carries who it is FROM, resolved to names here rather than
    left as ids for the caller to join by hand.
    """
    from sqlmodel import col, select

    from app.services.documents.models import DocumentTag
    from app.services.documents.service import DocumentService
    from app.services.matters.models import PARTY_TAG_PREFIX
    from app.services.matters.service import PartyService

    rows, _total = await DocumentService(db).list_documents(
        owner_user_id=owner_user_id, kind=kind, page_size=SHELF_PAGE
    )
    if q and q.strip():
        needle = q.strip().casefold()
        rows = [row for row in rows if needle in (row.title or "").casefold()]
    if not rows:
        return []

    # One query for every party tag on this page, rather than one per
    # document: a shelf listing must not be an N+1.
    ids = [row.id for row in rows]
    tags = (
        await db.exec(
            select(DocumentTag)
            .where(col(DocumentTag.document_id).in_(ids))
            .where(col(DocumentTag.label).startswith(PARTY_TAG_PREFIX))
        )
    ).all()
    senders: dict[int, list[int]] = {}
    for tag in tags:
        party_id = tag.label.removeprefix(PARTY_TAG_PREFIX)
        if party_id.isdigit():
            senders.setdefault(tag.document_id, []).append(int(party_id))

    names = await PartyService(db).names(
        sorted({pid for found in senders.values() for pid in found})
    )

    listed = []
    for row in rows:
        found = senders.get(row.id, [])
        if unattributed and found:
            continue
        listed.append(
            {
                "id": row.id,
                "title": row.title,
                "kind": row.kind,
                "form_type": row.form_type,
                "tax_year": row.tax_year,
                "document_date": row.document_date.isoformat()
                if row.document_date
                else None,
                "pages": row.page_count,
                "from": [
                    {"party_id": pid, "name": names.get(pid, "")} for pid in found
                ],
            }
        )
    return listed
