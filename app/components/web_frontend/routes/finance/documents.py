"""An account's paper: filing it, viewing it, and correcting what it says.

Split out of ``accounts.py`` at the 500-line budget, and the seam is a
real one: this module knows the documents service and the accounts
module does not. The only thing the account pages need back is how many
documents an account has, which they ask for by name.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from starlette.responses import Response

from app.components.web_frontend.documents import document_dialog, save_document
from app.components.web_frontend.filters import mark_new
from app.components.web_frontend.nav import account_tabs, section
from app.components.web_frontend.rendering import (
    dialog_done,
    render,
    where_from,
)
from app.components.web_frontend.routes.finance.accounts import (
    _header_context,
    _one_account,
)
from app.components.web_frontend.seen import remember, watermark
from app.services.finance.constants import account_tag
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.service import FinanceService

SECTION = section("accounts")
router = APIRouter()


# One path for a filed document; the viewer and the edit form both hang
# off it, so the account id cannot drift out of one of them.
DOCUMENTS = SECTION.path + "/{account_id:int}/documents/{document_id:int}"


async def _filed_document(
    service: FinanceService, account_id: int, document_id: int
) -> Any:
    """The document, if it is filed against THIS account.

    The URL names both, and filing is what makes a document reachable
    from an account - so a number guessed into the path gets a 404
    rather than somebody else's paper.
    """
    from app.services.documents.service import DocumentService

    documents = DocumentService(service.db)
    found = await documents.get(document_id)
    if found is None or account_tag(account_id) not in await documents.tags_for(
        document_id
    ):
        raise HTTPException(status_code=404)
    return found


@router.get(SECTION.path + "/{account_id:int}/documents", include_in_schema=False)
async def documents_page(
    request: Request,
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """The paper filed against this account, on its own page."""
    _, selected, context = await _one_account(service, owner_user_id, account_id)
    filed = await mark_new(
        service.db,
        await account_documents(service, account_id),
        watermark(request, "documents"),
    )
    response = render(
        request,
        "pages/account_documents.html",
        {
            **context,
            **account_tabs(account_id, "documents", len(filed)),
            **await _header_context(service, selected, owner_user_id),
            "documents": filed,
            "document_columns": list(DOCUMENT_COLUMNS),
        },
    )
    return remember(request, response, "documents")


@router.get(DOCUMENTS, include_in_schema=False)
async def document(
    request: Request,
    account_id: int,
    document_id: int,
    service: FinanceService = Depends(get_finance_service),
) -> Response:
    """One filed document: the original, its details, and what was read."""
    found = await _filed_document(service, account_id, document_id)
    return await document_dialog(
        request, service.db, found, _post(account_id, document_id)
    )


@router.post(DOCUMENTS, include_in_schema=False)
async def document_save(
    request: Request,
    account_id: int,
    document_id: int,
    title: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "other",
    document_date: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
) -> Response:
    """Save what we say about the document. A refusal re-renders the
    whole dialog, page beside form, so the reader never loses what they
    were looking at."""
    found = await _filed_document(service, account_id, document_id)
    errors = await save_document(
        service.db,
        document_id,
        title=title,
        kind=kind,
        document_date=document_date,
        note=note,
    )
    if errors:
        return await document_dialog(
            request, service.db, found, _post(account_id, document_id), 422, errors
        )
    # Back to the tab it was opened from. Documents, usually - and
    # landing on Overview after editing a document is the app deciding
    # you meant to go somewhere else.
    return dialog_done(
        where_from(request, f"{SECTION.path}/{account_id}/documents"),
        f"Saved {title.strip()}",
    )


def _post(account_id: int, document_id: int) -> str:
    return f"{SECTION.path}/{account_id}/documents/{document_id}"


# What a value history shows. ``note`` is what HAPPENED - a sale, a
# listing, a price change - because a column of numbers cannot tell a
# sale from an asking price.


async def account_documents(
    service: FinanceService, account_id: int
) -> list[dict[str, Any]]:
    """The paper filed against this account.

    A document belongs to an account by TAG - the document service says
    outright that what a tag means differs per application and the
    framework has no business guessing - so steward's meaning is this
    one label, written in one place so nothing has to re-derive it.
    """
    from app.services.documents.service import DocumentService

    # The REQUEST's session, never a second one. Every transaction takes
    # the write lock now (see ``_async_sqlite_emit_begin``), so a nested
    # session inside a request waits for a lock its own caller is
    # holding and times out as "database is locked" - a read deadlocking
    # against a read, which is the one thing the lock change made
    # possible.
    documents, _ = await DocumentService(service.db).list_documents(
        tag=account_tag(account_id)
    )
    from app.components.web_frontend.filters import short_date
    from app.components.web_frontend.glyphs import file_badge

    # Shaped here, not in the template: Jinja has no comprehension, and
    # a table's rows are data anyway.
    return [
        {
            "title": {
                "label": d.title,
                "url": f"{SECTION.path}/{account_id}/documents/{d.id}",
                "badge": file_badge(d.media_type, d.title),
            },
            "kind": d.kind,
            "at": short_date(d.document_date or d.received_at),
            "pages": d.page_count or "",
            # What the mark reads: when this arrived, and the run that
            # brought it if one did.
            "import_batch_id": d.import_batch_id,
            "created_at": d.created_at,
        }
        for d in documents
    ]


DOCUMENT_COLUMNS = (
    {"key": "title", "label": "Title", "kind": "open"},
    {"key": "kind", "label": "Kind"},
    {"key": "at", "label": "Dated"},
    {"key": "pages", "label": "Pages"},
)
