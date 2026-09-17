"""Turning what a document says about itself into a card.

The seam ST-08 is about: extraction ends, a reading begins, and what it
found lands in the same approval queue as every other proposed write.
Nothing here touches the document.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.documents.domains.reading.findings import Page
from app.services.documents.domains.reading.metadata import read_document

if TYPE_CHECKING:
    from app.services.documents.domains.reading.letters import LetterReading

# What reads a letter's demands: pages in, a reading out. Injected so a
# test can hand over a fake one and the worker can hand over None.
LetterReader = Callable[[list[Page]], Awaitable["LetterReading"]]

METADATA = "document.metadata"
REQUEST = "document.request"
# Whose cards these are, so a reading's work is visible as its own.
PROPOSED_BY = "reading"


async def _already_asked(db: AsyncSession, change_type: str, document_id: int) -> bool:
    """A pending card for this document is the answer already waiting.
    Reading again must not stack a second one on top of it."""
    from app.services.finance.domains.writes.queue import list_changes

    return any(
        change.payload.get("document_id") == document_id
        for change in await list_changes(db, status="pending")
        if change.change_type == change_type
    )


async def propose_reading(
    db: AsyncSession,
    document_id: int,
    *,
    owner_user_id: int | None = None,
    read_letter: LetterReader | None = None,
) -> Any | None:
    """Read a document and put what it says in front of somebody.

    Two readings, and each stands on its own. What the document says
    about ITSELF is read by pattern, always. What it DEMANDS is read by
    a model, and only when the document is filed on a matter - a request
    with no matter has nothing to be a request on, and a model call on
    every scrap of paper is a bill nobody agreed to.
    """
    request = await _propose_demands(
        db, document_id, owner_user_id=owner_user_id, read_letter=read_letter
    )
    return await _propose_metadata(db, document_id, owner_user_id) or request


async def _propose_demands(
    db: AsyncSession,
    document_id: int,
    *,
    owner_user_id: int | None,
    read_letter: LetterReader | None,
) -> Any | None:
    """What the letter asks for, as one card on its matter."""
    from app.services.documents.domains.reading.letters import checked
    from app.services.documents.queries import pages_for
    from app.services.finance.domains.writes.queue import propose
    from app.services.matters.matters import MatterService
    from app.services.matters.requests import RequestService

    if read_letter is None or await _already_asked(db, REQUEST, document_id):
        return None
    matter_id = await MatterService(db).for_document(document_id)
    if matter_id is None or await RequestService(db).citing(document_id):
        return None
    pages: list[Page] = [
        {"page": page.page_number, "text": page.text}
        for page in await pages_for(db, document_id)
    ]
    reading = checked(await read_letter(pages), pages)
    if reading is None:
        return None
    return await propose(
        db,
        REQUEST,
        {
            "document_id": document_id,
            "matter_id": matter_id,
            "received_on": reading.received_on.isoformat()
            if reading.received_on
            else None,
            "due_on": reading.due_on.isoformat() if reading.due_on else None,
            "items": [item.model_dump() for item in reading.items],
            "dropped": reading.dropped,
        },
        owner_user_id=owner_user_id,
        proposed_by_agent=PROPOSED_BY,
    )


async def _propose_metadata(
    db: AsyncSession, document_id: int, owner_user_id: int | None
) -> Any | None:
    """What the document says about itself.

    ``None`` when there is nothing to say: no findings, nothing the
    document does not already record, or a card still awaiting an answer.
    """
    from app.services.documents.queries import pages_for
    from app.services.documents.service import DocumentService
    from app.services.finance.domains.writes.queue import propose

    document = await DocumentService(db).get(document_id)
    if document is None or await _already_asked(db, METADATA, document_id):
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
        METADATA,
        payload,
        owner_user_id=owner_user_id,
        proposed_by_agent=PROPOSED_BY,
    )
