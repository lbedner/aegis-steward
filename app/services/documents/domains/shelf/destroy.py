"""Hard delete: the paper and everything that points at it, together.

``soft_delete`` retires a row and keeps the rest - the audit answer.
This is the other one, for paper that should never have been filed:
the two logos a mail import took for attachments before it knew a logo
from a statement (2026-09-21). The rows go in ONE transaction; the
bytes go after it commits, and only when no other document holds them.

This is the one module that knows everyone who points at a document.
The documents service is deliberately ignorant of what its paper
means, and mail and matters each point at it one way - so the list of
pointers has to live somewhere, and a delete that forgets one leaves
a row naming paper that is gone.
"""

from __future__ import annotations

from sqlalchemy import delete as sql_delete
from sqlalchemy import update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.documents.models import Document, DocumentPage, DocumentTag
from app.services.documents.service import DocumentService, ProtectedDocumentError
from app.services.mail.models import MailAttachment, MailBatch, MailMessage
from app.services.matters.models.core import DocumentParty, Fact, MatterEvent


async def destroy(
    db: AsyncSession,
    document_id: int,
    *,
    owner_user_id: int | None,
    confirm: str | None = None,
) -> list[str] | None:
    """Delete the document and every row that points at it.

    Returns the storage keys nothing references any more - the caller
    unlinks them AFTER committing, so a crash between the two leaves
    an orphan blob and never a row pointing at nothing. None when the
    document is not this owner's. A protected document goes only when
    ``confirm`` is its exact title, as ``soft_delete`` asks.
    """
    document = await DocumentService(db).get(document_id, owner_user_id=owner_user_id)
    if document is None:
        return None
    if document.protected and confirm != document.title:
        raise ProtectedDocumentError(
            "This document is protected; confirm by sending its exact title."
        )

    pages = (
        await db.exec(
            select(DocumentPage).where(DocumentPage.document_id == document_id)
        )
    ).all()
    orphaned = [page.image_key for page in pages if page.image_key]
    for page in pages:
        await db.delete(page)
    for tag in (
        await db.exec(select(DocumentTag).where(DocumentTag.document_id == document_id))
    ).all():
        await db.delete(tag)
    for link in (
        await db.exec(
            select(MailAttachment).where(MailAttachment.document_id == document_id)
        )
    ).all():
        await db.delete(link)
    for named in (
        await db.exec(
            select(DocumentParty).where(DocumentParty.document_id == document_id)
        )
    ).all():
        await db.delete(named)
    # The letter IS its message: the row goes with it, and the batch once
    # it is empty, so a re-upload of the file is a fresh import rather
    # than "identical file, already have it" about paper that is gone.
    for message in (
        await db.exec(select(MailMessage).where(MailMessage.document_id == document_id))
    ).all():
        batch_id = message.batch_id
        # Its attachments stay on the shelf as paper; their link to a
        # message that is going does not.
        await db.execute(
            sql_delete(MailAttachment).where(MailAttachment.message_id == message.id)
        )
        await db.delete(message)
        await db.flush()
        left = (
            await db.exec(
                select(MailMessage.id).where(MailMessage.batch_id == batch_id)
            )
        ).first()
        if left is None:
            await db.execute(sql_delete(MailBatch).where(MailBatch.id == batch_id))
    # Pointers that are a column on something that stays.
    for table in (Fact, MatterEvent, Document):
        column = table.supersedes_id if table is Document else table.document_id
        await db.execute(
            update(table).where(column == document_id).values({column.key: None})
        )
    await db.flush()

    # The bytes, only when this was the last row holding them.
    others = (
        await db.exec(
            select(Document.id).where(
                Document.content_hash == document.content_hash,
                Document.id != document_id,
            )
        )
    ).first()
    if others is None:
        orphaned.append(document.storage_key)
    await db.delete(document)
    await db.flush()
    return orphaned
