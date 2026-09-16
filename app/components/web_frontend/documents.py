"""A filed document in the one modal, wherever it is filed.

An account has paper and so does a matter, and "open the document" is
the same act in both places: the original on the left, what we say
about it on the right, the text that was read underneath. The routes
differ only in where the form posts and what a 404 means, so the dialog
and the save live here and both callers read them.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Any

from fastapi import Request
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.responses import Response

from app.components.web_frontend.rendering import dialog

# The API route that serves the bytes. One place, because the viewer,
# the "open the original" link and the fallback all point at it.
DOCUMENT_API = "/api/v1/documents"


def content_url(document_id: int) -> str:
    return f"{DOCUMENT_API}/{document_id}/content"


async def document_dialog(
    request: Request,
    db: AsyncSession,
    document: Any,
    post: str,
    status_code: int = 200,
    errors: list[str] | None = None,
) -> Response:
    """The document beside what we say about it, in the one modal.

    Both halves in one body because they are read together: somebody
    opening a filed document is checking a figure against the page and
    correcting what it was filed as, and making that two dialogs is
    making them click twice to do one thing.
    """
    from app.components.web_frontend.filters import short_date
    from app.services.documents.models import DOCUMENT_KINDS
    from app.services.documents.queries import pages_for

    pages = await pages_for(db, document.id)
    return dialog(
        request,
        "partials/accounts/document.html",
        status_code,
        document=document,
        dated=short_date(document.document_date or document.received_at),
        content=content_url(document.id),
        kinds=DOCUMENT_KINDS,
        post=post,
        errors=errors or [],
        pages=[
            {
                "number": page.page_number,
                "read": page.status == "read",
                # How it was read, so a figure quoted off this page can
                # say where it came from.
                "method": page.method,
                "text": page.text or "",
                "detail": page.detail or "",
            }
            for page in pages
        ],
    )


async def save_document(
    db: AsyncSession,
    document_id: int,
    *,
    title: str,
    kind: str,
    document_date: str,
    note: str,
) -> list[str]:
    """Save what we SAY about a document, and give back what refused it.

    The bytes never change: a document is what arrived, and correcting
    it would make the record a lie. Errors come back as a list rather
    than an exception because every caller answers them the same way -
    by re-rendering the dialog with the page still beside the form.
    """
    from app.services.documents.service import DocumentService

    if not title.strip():
        return ["Give the document a title."]
    dated: date_type | None = None
    if document_date:
        try:
            dated = date_type.fromisoformat(document_date)
        except ValueError:
            return ["That date is not a date."]
    try:
        await DocumentService(db).update(
            document_id,
            {
                "title": title,
                "kind": kind,
                "document_date": dated,
                "note": note.strip() or None,
            },
        )
    except ValueError as exc:
        return [str(exc)]
    await db.commit()
    return []
