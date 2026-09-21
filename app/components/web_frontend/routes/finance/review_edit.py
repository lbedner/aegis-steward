"""Your own words on a card before you approve it.

A card off a From header says "Optum" because that is the domain; the
person reading it knows it is Optum Financial. A card is a proposal,
and a proposal can be corrected before it is answered - for the types
that opt in (``ChangeExecutor.editable``), through the one dialog, with
every field prefilled from what was proposed.

Its own router, included BEFORE review's: ``/changes/{id}/edit`` would
otherwise be claimed by the verb route's wildcard segment and answer
404 to "edit".
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import (
    dialog,
    dialog_done,
    or_404,
    where_from,
)
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.domains.writes.queue import get_change, revise
from app.services.finance.domains.writes.registry import ChangeExecutor, executor_for
from app.services.finance.service import FinanceService

SECTION = section("review")
router = APIRouter(prefix=SECTION.path)


def _edit_fields(
    executor: ChangeExecutor, payload: dict[str, Any]
) -> list[dict[str, Any]]:
    """The payload contract as form fields: free text for every string
    field, a select where the type names choices, nothing for lists.
    Prefilled from the card, so what you see is what was proposed."""
    fields: list[dict[str, Any]] = []
    for name, info in executor.payload_model.model_fields.items():
        if info.annotation not in (str, str | None):
            continue
        options = executor.choices.get(name)
        fields.append(
            {
                "name": name,
                "label": name.replace("_", " ").capitalize(),
                "value": payload.get(name) or "",
                "required": info.is_required(),
                "options": [{"id": o, "name": o} for o in options] if options else None,
            }
        )
    return fields


async def _edit_dialog(
    request: Request,
    change: Any,
    executor: ChangeExecutor,
    payload: dict[str, Any],
    status_code: int = 200,
    errors: list[str] | None = None,
) -> Response:
    return dialog(
        request,
        "partials/review/edit.html",
        status_code,
        title=executor.title,
        proposed_by=change.proposed_by_agent,
        post=f"{SECTION.path}/changes/{change.id}/edit",
        fields=_edit_fields(executor, payload),
        errors=errors or [],
    )


async def _editable(
    service: FinanceService, change_id: int, owner_user_id: int | None
) -> tuple[Any, ChangeExecutor]:
    """The card and its type, or 404: a decided card, or a type that is
    answered rather than edited, has no form."""
    change = or_404(
        await get_change(service.db, change_id, owner_user_id=owner_user_id)
    )
    executor = executor_for(change.change_type)
    or_404(change if change.status == "pending" and executor.editable else None)
    return change, executor


@router.get("/changes/{change_id:int}/edit", include_in_schema=False)
async def edit_change(
    request: Request,
    change_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Your own words on a card before you approve it."""
    change, executor = await _editable(service, change_id, owner_user_id)
    return await _edit_dialog(request, change, executor, change.payload)


@router.post("/changes/{change_id:int}/edit", include_in_schema=False)
async def save_change(
    request: Request,
    change_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    change, executor = await _editable(service, change_id, owner_user_id)
    form = await request.form()
    # Blank means "not given", so the contract's own defaults and
    # required-ness apply, rather than an empty string passing as a value.
    typed = {
        f["name"]: str(form.get(f["name"]) or "").strip()
        for f in _edit_fields(executor, change.payload)
    }
    payload = {k: v for k, v in typed.items() if v}
    try:
        await revise(service.db, change_id, payload, owner_user_id=owner_user_id)
    except ValueError as exc:
        # The dialog shows what was typed, not what was proposed.
        return await _edit_dialog(request, change, executor, typed, 422, [str(exc)])
    await service.db.commit()
    return dialog_done(where_from(request, SECTION.path), "Saved")
