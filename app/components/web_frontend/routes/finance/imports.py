"""File import: a review, then the import, each a job followed over SSE.

One persistent multipart form in the dialog holds the file; Preview and
Import post it to their own routes and swap ``#import-result``. Both run
as the API's own background job (``imports_job``), so the file is never
parsed in this process (#487); the follower element streams the job's
rendered frames from ``/jobs/{id}/events``, and the last frame is the
review or the summary (``partials/jobs/status.html``).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from starlette.responses import Response

from app.components.backend.api.finance.accounts import list_accounts
from app.components.backend.api.finance.imports import start_import_job
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import templates
from app.services.finance.constants import PREVIEW_DETAIL_CAP
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.service import FinanceService, imports_job

SECTION = section("accounts")
router = APIRouter(prefix=SECTION.path + "/import")

LANES = (
    ("statement", "Bank or card statement (OFX, QFX, QIF, CSV)"),
    ("investments", "Investment activity (Optum)"),
)


def _lands(by_account: dict[str, int]) -> list[dict[str, Any]]:
    """Per-account new rows, as the house ranked card's rows. One bar is
    not a ranking, it is "To add" restated, so one account gets none."""
    if len(by_account) < 2:
        return []
    ranked = sorted(by_account.items(), key=lambda item: -item[1])
    top = ranked[0][1] or 1
    return [
        {"label": name, "value": f"{count:,}", "ratio": count / top}
        for name, count in ranked
    ]


# The review is rendered by the job's frame, away from any route, so what
# it needs from here is published to the templates.
templates.env.filters["import_lands"] = _lands
templates.env.globals["import_review"] = {
    "cap": PREVIEW_DETAIL_CAP,
    "run_path": router.prefix,
}
templates.env.globals["import_frame"] = imports_job.frame


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


async def _follow(
    request: Request,
    file: UploadFile,
    account_id: str,
    kind: str,
    service: FinanceService,
    owner_user_id: int | None,
) -> Response:
    """Store the upload and answer with the follower (pattern 5). Only
    the cheap checks - size, an account you own - happen here."""
    try:
        job_id = await start_import_job(
            file,
            account_id=int(account_id) if account_id else None,
            kind=kind,
            service=service,
            owner_user_id=owner_user_id,
        )
    except HTTPException as exc:
        return _error(request, str(exc.detail))
    return _fragment(
        request, "partials/imports/started.html", job_id=job_id, file_name=file.filename
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
    kind = "investments-preview" if lane == "investments" else "preview"
    return await _follow(request, file, account_id, kind, service, owner_user_id)


@router.post("", include_in_schema=False)
async def run(
    request: Request,
    file: Annotated[UploadFile, File()],
    account_id: Annotated[str, Form()] = "",
    lane: Annotated[str, Form()] = "statement",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Start the import. A ledger needs its account before it is stored:
    the job could only fail without one."""
    if lane == "investments" and not account_id:
        return _error(request, imports_job.NEEDS_ACCOUNT)
    kind = "investments" if lane == "investments" else "import"
    return await _follow(request, file, account_id, kind, service, owner_user_id)
