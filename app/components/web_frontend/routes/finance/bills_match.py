"""Matching a payment to the bill it paid, and the queue that walks them.

Split out of ``bills`` at the budget. The seam is a real one: that
module answers "what is coming", and this one answers "was this charge
the one we were expecting" - a question asked of one stream at a time,
from a dialog, with a shortlist the app ranks rather than guesses.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request
from starlette.responses import Response

from app.components.backend.api.finance.recurring import (
    candidate_items,
    confirm_recurring,
    list_recurring,
    mute_recurring,
    resume_recurring,
    unmute_recurring,
)
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import (
    close_dialog,
    dialog,
    or_404,
    with_toast,
)
from app.components.web_frontend.routes.finance.bills import (
    REVIEW_LIMIT,
    _row_response,
    _stream,
    needs_review,
)
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.models import FinanceRecurringStream
from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date

SECTION = section("bills")
router = APIRouter(prefix=SECTION.path)


async def _match_dialog(
    request: Request,
    service: FinanceService,
    owner_user_id: int | None,
    stream: FinanceRecurringStream,
    queue: list[int],
    index: int,
) -> Response:
    """The shortlist for one bill. With a ``queue`` this is one step of a
    review session: the title carries the position and Skip moves on."""
    assert stream.id is not None
    rows = await service.recurring_match_candidates(
        stream.id, owner_user_id=owner_user_id
    )
    candidates = await candidate_items(service, rows)
    queue_csv = ",".join(map(str, queue))
    return dialog(
        request,
        "partials/bills/match.html",
        stream=stream,
        candidates=candidates,
        position=f" ({index + 1} of {len(queue)})" if queue else "",
        queue=queue_csv,
        index=index,
        next_url=(
            f"{SECTION.path}/review?ids={queue_csv}&index={index + 1}"
            if index + 1 < len(queue)
            else ""
        ),
    )


async def _review_queue(
    service: FinanceService, owner_user_id: int | None
) -> list[int]:
    """The overdue curated bills that have at least one candidate payment."""
    listing = await list_recurring(service=service, owner_user_id=owner_user_id)
    today = current_date()
    queue: list[int] = []
    for stream in listing.items:
        if len(queue) >= REVIEW_LIMIT:
            break
        if needs_review(stream, today) and await service.recurring_match_candidates(
            stream.id, owner_user_id=owner_user_id
        ):
            queue.append(stream.id)
    return queue


def _ids(csv: str) -> list[int]:
    return [int(part) for part in csv.split(",") if part.strip()]


@router.get("/{stream_id:int}/match", include_in_schema=False)
async def match_form(
    request: Request,
    stream_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    stream = await _stream(service, stream_id, owner_user_id)
    return await _match_dialog(request, service, owner_user_id, stream, [], 0)


@router.get("/review", include_in_schema=False)
async def review(
    request: Request,
    ids: str = "",
    index: int = Query(default=0, ge=0),
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Walk the overdue bills that have a candidate payment, one dialog
    at a time. The first call computes the queue; later steps carry it."""
    queue = _ids(ids) if ids else await _review_queue(service, owner_user_id)
    if not queue:
        # The button counts overdue bills; the queue needs a payment to
        # offer. Nothing to show means no swap (204), or the dialog would
        # open empty and close on the same tick.
        return with_toast(
            Response(status_code=204),
            "Nothing to review yet: no imported payment looks like an overdue bill.",
        )
    if index >= len(queue):
        return close_dialog(Response(status_code=200))
    stream = await _stream(service, queue[index], owner_user_id)
    return await _match_dialog(request, service, owner_user_id, stream, queue, index)


@router.post("/{stream_id:int}/match", include_in_schema=False)
async def match(
    request: Request,
    stream_id: int,
    transaction_id: Annotated[int, Form()],
    queue: Annotated[str, Form()] = "",
    index: Annotated[int, Form()] = 0,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Attach the payment: the row comes back out of band and the dialog
    closes, or shows the next bill of a review session."""
    stream = await _stream(service, stream_id, owner_user_id)
    attached = await service.attach_transaction_to_stream(
        transaction_id, stream_id, owner_user_id=owner_user_id
    )
    or_404(attached)
    row_response = await _row_response(
        request, service, attached, owner_user_id, oob=True
    )
    due = attached.next_expected_date
    toast = f"Matched. {stream.name} is paid; next expected {due or 'never'}."
    remaining = _ids(queue)
    if index + 1 < len(remaining):
        step = await _match_dialog(
            request,
            service,
            owner_user_id,
            await _stream(service, remaining[index + 1], owner_user_id),
            remaining,
            index + 1,
        )
        merged = Response(content=step.body + row_response.body, media_type="text/html")
        return with_toast(merged, toast)
    return close_dialog(with_toast(row_response, toast))


# --- row verbs: last, so the literal paths above win ----------------------

VERBS = {
    "confirm": confirm_recurring,
    "resume": resume_recurring,
    "mute": mute_recurring,
    "unmute": unmute_recurring,
}


@router.post("/{stream_id:int}/{verb}", include_in_schema=False)
async def verb(
    request: Request,
    stream_id: int,
    verb: str,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    handler = VERBS.get(verb)
    or_404(handler)
    await _stream(service, stream_id, owner_user_id)
    await handler(stream_id, service=service, owner_user_id=owner_user_id)  # type: ignore[call-arg]
    stream = await _stream(service, stream_id, owner_user_id)
    return await _row_response(request, service, stream, owner_user_id)
