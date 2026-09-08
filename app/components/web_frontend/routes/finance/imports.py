"""File import: preview, then a background job followed over SSE.

One persistent multipart form in the dialog holds the file; Preview and
Import post it to their own routes and swap ``#import-result``. The
import runs as the API's own background job; the follower element
streams the job's rendered frames from ``/jobs/{id}/events``.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from starlette.responses import JSONResponse, Response

from app.components.backend.api.finance.accounts import list_accounts
from app.components.backend.api.finance.imports import import_file, preview_import
from app.components.backend.api.finance.investments import (
    import_investments,
    preview_import_investments,
)
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import templates
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.service import FinanceService

SECTION = section("accounts")
router = APIRouter(prefix=SECTION.path + "/import")

LANES = (
    ("statement", "Bank or card statement (OFX, QFX, QIF, CSV)"),
    ("investments", "Investment activity (Optum)"),
)


def _fragment(
    request: Request, name: str, status_code: int = 200, **context: Any
) -> Response:
    return templates.TemplateResponse(
        request=request, name=name, context=context, status_code=status_code
    )


def _error(request: Request, detail: str, status_code: int = 422) -> Response:
    return _fragment(
        request, "partials/imports/error.html", status_code, errors=[detail]
    )


@router.get("", include_in_schema=False)
async def form(
    request: Request,
    account_id: int | None = None,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    listing = await list_accounts(
        include_hidden=False,
        page=1,
        page_size=200,
        service=service,
        owner_user_id=owner_user_id,
    )
    return _fragment(
        request,
        "partials/imports/form.html",
        accounts=listing.items,
        account_id=account_id,
        lanes=LANES,
    )


@router.post("/preview", include_in_schema=False)
async def preview(
    request: Request,
    file: Annotated[UploadFile, File()],
    account_id: Annotated[str, Form()] = "",
    lane: Annotated[str, Form()] = "statement",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """A dry run: what a commit would do, from the same plan it executes."""
    target = int(account_id) if account_id else None
    try:
        if lane == "investments":
            result = await preview_import_investments(file=file, profile="optum")
            return _fragment(
                request, "partials/imports/preview_investments.html", preview=result
            )
        result = await preview_import(
            file=file, account_id=target, service=service, owner_user_id=owner_user_id
        )
    except HTTPException as exc:
        return _error(request, str(exc.detail))
    if result.needs_account:
        return _error(
            request, "This file carries no account; choose the account it belongs to."
        )
    return _fragment(request, "partials/imports/preview.html", preview=result)


@router.post("", include_in_schema=False)
async def run(
    request: Request,
    file: Annotated[UploadFile, File()],
    account_id: Annotated[str, Form()] = "",
    lane: Annotated[str, Form()] = "statement",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Start the import. Statements run as the API's background job and
    the response is the follower; investment ledgers import inline."""
    target = int(account_id) if account_id else None
    try:
        if lane == "investments":
            result = await import_investments(
                file=file,
                account_id=target,
                account_name=None,
                profile="optum",
                service=service,
                owner_user_id=owner_user_id,
            )
            await service.db.commit()
            return _fragment(
                request, "partials/imports/summary_investments.html", result=result
            )
        started = await import_file(
            file=file,
            account_id=target,
            background=True,
            service=service,
            owner_user_id=owner_user_id,
        )
    except HTTPException as exc:
        return _error(request, str(exc.detail))
    assert isinstance(started, JSONResponse)
    job_id = json.loads(bytes(started.body))["job_id"]
    return _fragment(
        request, "partials/imports/started.html", job_id=job_id, file_name=file.filename
    )
