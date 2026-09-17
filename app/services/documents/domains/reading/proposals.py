"""Turning what a document says about itself into a card.

The seam ST-08 is about: extraction ends, a reading begins, and what it
found lands in the same approval queue as every other proposed write.
Nothing here touches the document.
"""

from __future__ import annotations

from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.documents.domains.reading.metadata import read_document

CHANGE_TYPE = "document.metadata"
# Whose cards these are, so a reading's work is visible as its own.
PROPOSED_BY = "reading"


async def _already_asked(db: AsyncSession, document_id: int) -> bool:
    """A pending card for this document is the answer already waiting.
    Reading again must not stack a second one on top of it."""
    from app.services.finance.domains.writes.queue import list_changes

    return any(
        change.payload.get("document_id") == document_id
        for change in await list_changes(db, status="pending")
        if change.change_type == CHANGE_TYPE
    )


async def propose_reading(
    db: AsyncSession, document_id: int, *, owner_user_id: int | None = None
) -> Any | None:
    """Read the document's pages and propose what it says about itself.

    ``None`` when there is nothing to say: no findings, nothing the
    document does not already record, or a card still awaiting an answer.
    """
    from app.services.documents.queries import pages_for
    from app.services.documents.service import DocumentService
    from app.services.finance.domains.writes.queue import propose

    document = await DocumentService(db).get(document_id)
    if document is None or await _already_asked(db, document_id):
        return None
    pages = await pages_for(db, document_id)
    payload: dict[str, Any] = {"document_id": document_id}
    for found in read_document(
        [{"page": page.page_number, "text": page.text} for page in pages]
    ):
        # A card that changes nothing wastes a decision.
        standing = getattr(document, found.field, None)
        if str(standing or "") == str(found.value):
            continue
        payload[found.field] = {
            "value": str(found.value),
            "page": found.page,
            "because": found.because,
        }
    if len(payload) == 1:
        return None
    return await propose(
        db,
        CHANGE_TYPE,
        payload,
        owner_user_id=owner_user_id,
        proposed_by_agent=PROPOSED_BY,
    )
