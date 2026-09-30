"""Approval cards in the thread (#212): a change she proposed, or a batch
of them, drawn from the trace's markers and approved or rejected in place.

Moved out of ``chat.py`` (its line budget), unchanged: the helpers shape a
pending change into its card, the routes serve and resolve it.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from starlette.responses import Response

from app.components.backend.api.finance.changes import (
    approve_batch,
    approve_change,
    get_batch,
    get_change,
    reject_batch,
    reject_change,
)
from app.components.web_frontend.rendering import (
    or_404,
    templates,
    trigger,
    with_toast,
)
from app.components.web_frontend.routes.chat import COMPONENTS
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.schemas.changes import (
    BatchResolveRequest,
    PendingChangeResponse,
)
from app.services.finance.service import FinanceService

router = APIRouter()


STATUS_COPY = {
    "pending": ("Awaiting your approval", "warn"),
    "approved": ("Approved", "ok"),
    "rejected": ("Rejected", "error"),
    "withdrawn": ("Withdrawn", "muted"),
    "expired": ("Expired", "muted"),
}


def status_of(change: PendingChangeResponse) -> tuple[str, str, str | None]:
    """The status word to show, its tone, and a note under it. A
    withdrawal lands in the queue as a rejection with a note, so the
    audit trail stays one shape; on the card it is the assistant taking
    its own proposal back, and its reason is the line worth reading."""
    status = change.status
    note = change.note
    if status == "rejected" and note and note.startswith("Withdrawn"):
        status = "withdrawn"
    else:
        note = None
    label, tone = STATUS_COPY.get(status, (status.title(), "muted"))
    return label, tone, note


def change_card(change: PendingChangeResponse) -> dict[str, Any]:
    label, tone, note = status_of(change)
    return {
        "change": change,
        "status": label,
        "tone": tone,
        "note": note,
        "pending": change.status == "pending",
    }


def same_change(change: PendingChangeResponse) -> tuple[Any, ...]:
    """What makes two proposals THE SAME proposal, for grouping.

    Everything the card says except which row it is about: the payee and
    every label/value line under the subject. Seventy-one rows tagged
    Pool Loan differ only in a date, and reading seventy-one of them is
    not review, it is scrolling.

    The payee is part of it because the group's summary NAMES one, and
    "71 transactions · GreenSky" is only true if they all are.
    """
    return (
        change.payee,
        tuple((row.label, row.value) for row in change.display[1:]),
    )


def group_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows folded into groups of identical changes, in first-seen order.

    A group carries the rows themselves, so the per-row veto survives
    behind an expander: what collapses is the READING, never the
    control. One row is never a group - a wrapper around a single thing
    is just another thing to open.
    """
    order: list[tuple[Any, ...]] = []
    found: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        key = same_change(row["change"])
        if key not in found:
            order.append(key)
            found[key] = []
        found[key].append(row)
    return [summarize(found[key]) for key in order]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """One group: what its rows have in common, and what varies."""
    first = rows[0]["change"]
    subjects = [r["change"].display[0] for r in rows if r["change"].display]
    amounts = [s.amount for s in subjects if s.amount is not None]
    dates = sorted(s.at for s in subjects if s.at)
    return {
        "rows": rows,
        "change": first,
        "count": len(rows),
        # The number worth seeing before approving seventy-one of
        # anything, and the one the card never showed.
        "total": sum(amounts) if len(amounts) == len(rows) else None,
        "span": (dates[0], dates[-1]) if dates else None,
        "pending": any(r["pending"] for r in rows),
        "single": len(rows) == 1,
    }


def batch_card(batch_id: str, items: list[PendingChangeResponse]) -> dict[str, Any]:
    rows = [change_card(c) for c in items]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"].lower()] = counts.get(row["status"].lower(), 0) + 1
    return {
        "batch_id": batch_id,
        "title": items[0].title if items else "",
        "rows": rows,
        "groups": group_rows(rows),
        "pending": any(r["pending"] for r in rows),
        "outcome": ", ".join(f"{n} {word}" for word, n in sorted(counts.items())),
    }


def _gone(request: Request) -> Response:
    """The answer for a card whose subject is no longer there.

    A transcript is a record and the queue moves on: a proposal gets
    purged, or a whole ledger is replaced under one. The card loads
    itself when the page opens, so a 404 here reaches the reader as an
    error toast every single visit — for something that is simply
    history. It says so instead, and stays a 200.
    """
    return templates.TemplateResponse(request=request, name="partials/chat/gone.html")


async def _card(
    request: Request,
    name: str,
    card: dict[str, Any],
    service: FinanceService,
    owner_user_id: int | None,
) -> Response:
    """A card, and when the page behind it is a Review queue, that page's
    counts out of band: a decision made in the drawer must reach the tab
    bar the reader is looking at. The tab stays whichever one shows."""
    from app.components.web_frontend.routes.finance import review

    context: dict[str, Any] = {"card": card, "base": COMPONENTS}
    current = request.headers.get("HX-Current-URL", "")
    path = current.split("//", 1)[-1].split("/", 1)[-1] if "//" in current else current
    if ("/" + path).startswith(review.SECTION.path):
        tab = next(
            (
                key
                for key, _l, suffix in review.QUEUES
                if ("/" + path).startswith(review.SECTION.path + suffix) and suffix
            ),
            "approvals",
        )
        context.update(
            await review.nav_context(service, owner_user_id, tab), nav_oob=True
        )
    return templates.TemplateResponse(request=request, name=name, context=context)


@router.get(COMPONENTS + "/change/{change_id:int}", include_in_schema=False)
async def change_component(
    request: Request,
    change_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    try:
        change = await get_change(
            change_id, service=service, owner_user_id=owner_user_id
        )
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return _gone(request)
    return await _card(
        request,
        "partials/chat/change.html",
        change_card(change),
        service,
        owner_user_id,
    )


@router.post(COMPONENTS + "/change/{change_id:int}/{verb}", include_in_schema=False)
async def resolve_change_component(
    request: Request,
    change_id: int,
    verb: str,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """The card re-renders from whatever the queue says afterwards, never
    an optimistic guess."""
    handler = {"approve": approve_change, "reject": reject_change}.get(verb)
    or_404(handler)
    refused: str | None = None
    try:
        change = await handler(change_id, service=service, owner_user_id=owner_user_id)
    except HTTPException as exc:
        # A refused execution is an ANSWER, not a failure to respond.
        # The queue already recorded why and left the row pending (the
        # decision is still the user's), but a 400 does not swap - see
        # the htmx-config responseHandling - so letting it out meant
        # clicking Approve did nothing at all, forever, with no hint
        # that the split did not add up. Re-render the card: it carries
        # the recorded error, and the toast says it out loud.
        refused = str(exc.detail)
        await service.db.commit()
        change = await get_change(
            change_id, service=service, owner_user_id=owner_user_id
        )
    await service.db.commit()
    response = await _card(
        request,
        "partials/chat/change.html",
        change_card(change),
        service,
        owner_user_id,
    )
    if refused is not None:
        return with_toast(response, refused, tone="error")
    # So the page behind can redraw what it changed (pages/contact.html).
    return trigger(response, "change:resolved")


@router.get(COMPONENTS + "/batch/{batch_id}", include_in_schema=False)
async def batch_component(
    request: Request,
    batch_id: str,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    listing = await get_batch(batch_id, service=service, owner_user_id=owner_user_id)
    if not listing.items:
        return _gone(request)
    return await _card(
        request,
        "partials/chat/batch.html",
        batch_card(batch_id, listing.items),
        service,
        owner_user_id,
    )


@router.post(COMPONENTS + "/batch/{batch_id}/{verb}", include_in_schema=False)
async def resolve_batch_component(
    request: Request,
    batch_id: str,
    verb: str,
    exclude_ids: Annotated[list[int], Form()] = [],
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    if verb == "approve":
        await approve_batch(
            batch_id,
            BatchResolveRequest(exclude_ids=exclude_ids),
            service=service,
            owner_user_id=owner_user_id,
        )
    elif verb == "reject":
        await reject_batch(batch_id, service=service, owner_user_id=owner_user_id)
    else:
        raise HTTPException(status_code=404)
    await service.db.commit()
    listing = await get_batch(batch_id, service=service, owner_user_id=owner_user_id)
    response = await _card(
        request,
        "partials/chat/batch.html",
        batch_card(batch_id, listing.items),
        service,
        owner_user_id,
    )
    return trigger(response, "change:resolved")
