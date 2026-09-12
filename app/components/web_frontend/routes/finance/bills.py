"""Bills & Income: recurring streams, their row actions, and the dialogs.

One page route partitions the streams into Bills, Income and Detected
(``?tab=``) and re-renders the table in place as filters change (pattern
3). Every row action answers with the row (pattern 2); the editor, pause,
categorize and match dialogs are pattern 4 around pattern 1. The API's
handlers do the work in-process; this module owns the commit.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from starlette.responses import Response

from app.components.backend.api.finance.categories import list_category_options
from app.components.backend.api.finance.recurring import (
    candidate_items,
    confirm_recurring,
    hydrate_streams,
    list_recurring,
    mute_recurring,
    pause_recurring,
    rescan_recurring,
    resume_recurring,
    unmute_recurring,
)
from app.components.web_frontend import ranges
from app.components.web_frontend.filters import cents_to_input, money_to_cents
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import (
    close_dialog,
    dialog,
    navigate,
    render,
    templates,
    with_toast,
)
from app.services.finance.constants import (
    BILL_FREQUENCY_OPTIONS,
    PAUSE_INDEFINITE,
    add_months,
    frequency_label,
)
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.domains.detection.insights.commitments import (
    is_paused,
)
from app.services.finance.models import FinanceRecurringStream
from app.services.finance.schemas import RecurringPause, RecurringStreamResponse
from app.services.finance.service import FinanceService
from app.services.finance.utils import FREQUENCY_STEPS, current_date

SECTION = section("bills")
router = APIRouter(prefix=SECTION.path)

TABS: tuple[tuple[str, str], ...] = (
    ("bills", "Bills"),
    ("income", "Income"),
    ("detected", "Detected"),
)
COLUMNS = [
    {"key": "name", "label": "Name", "kind": "avatar"},
    {"key": "category", "label": "Category"},
    {"key": "account", "label": "Account"},
    {"key": "amount", "label": "Amount", "kind": "money", "align": "right"},
    {"key": "cadence", "label": "Cadence"},
    {"key": "next_due", "label": "Next due", "kind": "date"},
    {"key": "health", "label": "Health", "kind": "status"},
    {"key": "state", "label": "Status", "kind": "status"},
]
# staleness (the API's stream_staleness) -> what the Health cell says.
HEALTH: dict[str, tuple[str, str]] = {
    "fresh": ("Active", "ok"),
    "overdue": ("Overdue", "warn"),
    "stale": ("Stale", "error"),
}
DIRECTIONS = [{"id": "outflow", "name": "Bill"}, {"id": "inflow", "name": "Income"}]
FREQUENCIES = [
    {"id": key, "name": label} for key, label in BILL_FREQUENCY_OPTIONS.items()
]
PAUSE_MONTHS = 3
REVIEW_LIMIT = 50


# --- presentation rules (pure) -----------------------------------------


def is_curated(stream: RecurringStreamResponse) -> bool:
    """A stream the user vouched for: hand-entered or confirmed. The
    detector's own guesses (including its subscription flag) wait in
    Detected until confirmed."""
    return stream.source == "user" or stream.is_user_confirmed


def past_due(stream: RecurringStreamResponse, today: date) -> bool:
    return stream.next_expected_date is not None and stream.next_expected_date < today


def tab_of(stream: RecurringStreamResponse) -> str:
    """Bills and Income hold curated, unmuted rows; everything else (the
    detector's proposals in both directions, anything muted) is Detected,
    so Unmute stays reachable."""
    if not is_curated(stream) or stream.is_muted:
        return "detected"
    return "income" if stream.direction == "inflow" else "bills"


def needs_review(stream: RecurringStreamResponse, today: date) -> bool:
    """A curated bill whose due date has passed and is still worth chasing
    (no grace window: Review is the user asking "what has passed?")."""
    return (
        stream.direction == "outflow"
        and is_curated(stream)
        and not stream.is_muted
        and not is_paused(stream, today)
        and stream.staleness != "stale"
        and past_due(stream, today)
    )


def health(stream: RecurringStreamResponse, today: date) -> dict[str, str]:
    """Display truth over grace: the API's "fresh" tolerates settlement
    lag, but a row past its due date reading Active is a lie."""
    key = stream.staleness
    if key == "fresh" and past_due(stream, today):
        key = "overdue"
    # A paused bill is not late, whichever side said it was: "skip my
    # investments for a few months" is the user saying not to expect it,
    # and the due date it has already passed is exactly what they
    # paused. Reading Paused in one column and Overdue in the next is
    # the row arguing with itself. Applied AFTER, because the API's own
    # staleness already says "overdue" on its own - guarding only the
    # line above left the two live bills that prompted this still
    # reading Overdue. Stale survives: a bill nothing has paid in months
    # is stale whether or not it is paused, and that is worth saying.
    if key == "overdue" and is_paused(stream, today):
        key = "fresh"
    label, tone = HEALTH.get(key, HEALTH["fresh"])
    return {"label": label, "tone": tone}


def next_due(stream: RecurringStreamResponse, today: date) -> date | None:
    """When this bill is next expected - which for a paused one is not
    the date it was already past when it was paused.

    Sep 3 on a bill paused indefinitely is not a date anybody is waiting
    for: it is the occurrence the pause skipped, and leaving it there
    kept two investment bills sitting at the top of a list ordered by
    what is due soonest. A pause with an END has a real answer - the
    first occurrence on or after the day it comes back - and an
    indefinite one has none, so it says none and sorts last.
    """
    if not is_paused(stream, today):
        return stream.next_expected_date
    if stream.paused_until is None or stream.paused_until >= PAUSE_INDEFINITE:
        return None
    step = FREQUENCY_STEPS.get(stream.frequency)
    when = stream.next_expected_date
    if step is None or when is None:
        return None
    # Walk rather than divide: the cadences are months and weeks, not a
    # fixed number of days, and the step is the one definition of how
    # each one advances. Bounded by the cadence count in a century.
    for _ in range(1200):
        if when >= stream.paused_until:
            return when
        when = step(when)
    return None


def state(stream: RecurringStreamResponse, today: date) -> dict[str, str]:
    if stream.is_muted:
        return {"label": "Muted", "tone": "muted"}
    if is_paused(stream, today):
        return {"label": "Paused", "tone": "muted"}
    if stream.is_payment:
        return {"label": "Payment", "tone": "accent"}
    if stream.direction == "inflow" or is_curated(stream):
        return {"label": "Good", "tone": "ok"}
    return {"label": "Detected", "tone": "warn"}


def row(stream: RecurringStreamResponse, today: date) -> dict[str, Any]:
    cadence = frequency_label(stream.frequency)
    if stream.amount_is_variable:
        cadence = f"{cadence} · varies"
    return {
        "id": stream.id,
        "name": stream.name,
        "icon_url": stream.icon_url,
        "category": stream.category_name,
        "account": stream.account_name,
        "amount": stream.amount,
        "currency": stream.currency,
        "cadence": cadence,
        "next_due": next_due(stream, today),
        "health": health(stream, today),
        "state": state(stream, today),
        "direction": stream.direction,
        "curated": is_curated(stream),
        "muted": stream.is_muted,
        "paused": is_paused(stream, today),
        "pause_note": stream.pause_note,
    }


def matches(stream: RecurringStreamResponse, q: str) -> bool:
    needle = q.strip().lower()
    return not needle or needle in stream.name.lower()


def in_window(stream: RecurringStreamResponse, cutoff: date | None) -> bool:
    """Whether the window keeps this stream, by its last real payment.

    A bill nothing has matched yet (just declared, or detected from
    history the window excludes) has nothing to judge, so it stays rather
    than vanishing from the tab that exists to show it."""
    if cutoff is None or stream.last_date is None:
        return True
    return stream.last_date >= cutoff


# --- context ------------------------------------------------------------


async def streams_context(
    service: FinanceService,
    owner_user_id: int | None,
    tab: str,
    q: str,
    days: int = ranges.ALL,
) -> dict[str, Any]:
    """Everything ``components/streams.html`` renders for one tab."""
    listing = await list_recurring(service=service, owner_user_id=owner_user_id)
    today = current_date()
    tab = tab if tab in dict(TABS) else TABS[0][0]
    counts = {key: 0 for key, _label in TABS}
    shown: list[RecurringStreamResponse] = []
    cutoff = ranges.since(days)
    for stream in listing.items:
        if not matches(stream, q) or not in_window(stream, cutoff):
            continue
        counts[tab_of(stream)] += 1
        if tab_of(stream) == tab:
            shown.append(stream)
    due = [s.id for s in listing.items if needs_review(s, today)]
    return {
        "tab": tab,
        "q": q,
        "days": days,
        "ranges": ranges.WINDOWS,
        "tabs": [(key, f"{label} ({counts[key]})") for key, label in TABS],
        # Soonest first, and a bill with no date at all last: the list
        # answers "what is coming", and a paused bill is not coming.
        "rows": sorted(
            (row(s, today) for s in shown),
            key=lambda r: (r["next_due"] is None, r["next_due"] or today),
        ),
        "columns": COLUMNS,
        "monthly_cost": listing.monthly_cost,
        "review_ids": due,
        "path": SECTION.path,
    }


def _tab_query(tab: str, q: str) -> str:
    return f"tab={tab}" + (f"&q={q}" if q else "")


async def _stream(
    service: FinanceService, stream_id: int, owner_user_id: int | None
) -> FinanceRecurringStream:
    stream = await service.get_recurring(stream_id, owner_user_id)
    if stream is None or stream.deleted_at is not None:
        raise HTTPException(status_code=404)
    return stream


async def _row_response(
    request: Request,
    service: FinanceService,
    stream: FinanceRecurringStream,
    owner_user_id: int | None,
    oob: bool = False,
    status_code: int = 200,
) -> Response:
    """The row after an action: hydrated like the list renders it."""
    await service.db.commit()
    hydrated = await hydrate_streams(service, [stream], owner_user_id)
    return templates.TemplateResponse(
        request=request,
        name="partials/bills/rows.html",
        context={
            "rows": [row(hydrated[0], current_date())],
            "columns": COLUMNS,
            "oob": oob,
        },
        status_code=status_code,
    )


# --- the page and the table ---------------------------------------------


@router.get("", include_in_schema=False)
async def page(
    request: Request,
    tab: str = "bills",
    q: str = "",
    days: int = ranges.ALL,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    context = await streams_context(service, owner_user_id, tab, q, days)
    return render(request, "pages/bills.html", {"section": SECTION, **context})


@router.post("/rescan", include_in_schema=False)
async def rescan(
    request: Request,
    tab: Annotated[str, Form()] = "bills",
    q: Annotated[str, Form()] = "",
    days: Annotated[int, Form()] = ranges.ALL,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Re-run detection, then re-render the table for the current view."""
    result = await rescan_recurring(service=service, owner_user_id=owner_user_id)
    context = await streams_context(service, owner_user_id, tab, q, days)
    response = templates.TemplateResponse(
        request=request, name="components/streams.html", context=context
    )
    return with_toast(
        response, f"Rescan found {result.detected}, pruned {result.pruned}."
    )


@router.delete("/{stream_id:int}", include_in_schema=False)
async def delete(
    stream_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Soft-delete; the row's outerHTML swap with an empty body removes it."""
    stream = await _stream(service, stream_id, owner_user_id)
    await service.delete_recurring(stream_id, owner_user_id=owner_user_id)
    await service.db.commit()
    return with_toast(Response(status_code=200), f"Deleted {stream.name}.")


# --- pause --------------------------------------------------------------


@router.get("/{stream_id:int}/pause", include_in_schema=False)
async def pause_form(
    request: Request,
    stream_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    stream = await _stream(service, stream_id, owner_user_id)
    return dialog(
        request,
        "partials/bills/pause.html",
        stream=stream,
        until=add_months(current_date(), PAUSE_MONTHS),
        note="",
        errors=[],
    )


@router.post("/{stream_id:int}/pause", include_in_schema=False)
async def pause(
    request: Request,
    stream_id: int,
    until: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    stream = await _stream(service, stream_id, owner_user_id)
    try:
        when = date.fromisoformat(until)
    except ValueError:
        return dialog(
            request,
            "partials/bills/pause.html",
            422,
            stream=stream,
            until=until,
            note=note,
            errors=["Pick a date to pause until."],
        )
    await pause_recurring(
        stream_id,
        RecurringPause(until=when, note=note or None),
        service=service,
        owner_user_id=owner_user_id,
    )
    response = await _row_response(request, service, stream, owner_user_id, oob=True)
    return close_dialog(with_toast(response, f"Paused {stream.name}."))


# --- categorize ---------------------------------------------------------


@router.get("/{stream_id:int}/categorize", include_in_schema=False)
async def categorize_form(
    request: Request,
    stream_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    stream = await _stream(service, stream_id, owner_user_id)
    categories = await list_category_options(service=service)
    return dialog(
        request,
        "partials/bills/categorize.html",
        stream=stream,
        categories=categories.items,
    )


@router.post("/{stream_id:int}/categorize", include_in_schema=False)
async def categorize(
    request: Request,
    stream_id: int,
    category_id: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Set the category on the bill only; its transactions keep theirs."""
    stream = await _stream(service, stream_id, owner_user_id)
    if category_id:
        await service.update_recurring(
            stream_id, owner_user_id=owner_user_id, category_id=int(category_id)
        )
    response = await _row_response(request, service, stream, owner_user_id, oob=True)
    return close_dialog(response)


# --- editor -------------------------------------------------------------


async def _editor(
    request: Request,
    service: FinanceService,
    owner_user_id: int | None,
    stream: FinanceRecurringStream | None,
    values: dict[str, str],
    errors: list[str],
    status_code: int = 200,
) -> Response:
    accounts, _total = await service.list_accounts(
        owner_user_id=owner_user_id, page_size=500
    )
    return dialog(
        request,
        "partials/bills/editor.html",
        status_code,
        stream=stream,
        values=values,
        errors=errors,
        directions=DIRECTIONS,
        frequencies=FREQUENCIES,
        accounts=accounts,
    )


def _values(stream: FinanceRecurringStream | None) -> dict[str, str]:
    if stream is None:
        return {
            "name": "",
            "direction": "outflow",
            "frequency": "monthly",
            "expected_amount": "",
            "next_expected_date": "",
            "account_id": "",
        }
    return {
        "name": stream.name,
        "direction": stream.direction,
        "frequency": stream.frequency,
        "expected_amount": cents_to_input(stream.amount),
        "next_expected_date": (
            stream.next_expected_date.isoformat() if stream.next_expected_date else ""
        ),
        "account_id": str(stream.account_id or ""),
    }


def _parse(values: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    """The form's fields as the API wants them, or what is wrong with them."""
    errors: list[str] = []
    parsed: dict[str, Any] = {"name": values["name"].strip()}
    if not parsed["name"]:
        errors.append("Give it a name.")
    if values["direction"] not in {d["id"] for d in DIRECTIONS}:
        errors.append("Pick bill or income.")
    parsed["direction"] = values["direction"]
    if values["frequency"] not in BILL_FREQUENCY_OPTIONS:
        errors.append("Pick how often it repeats.")
    parsed["frequency"] = values["frequency"]
    cents = money_to_cents(values["expected_amount"])
    if cents is None or cents <= 0:
        errors.append("Enter the amount in dollars.")
    parsed["expected_amount"] = abs(cents or 0)
    try:
        parsed["next_expected_date"] = date.fromisoformat(values["next_expected_date"])
    except ValueError:
        errors.append("Pick the next due date.")
    parsed["account_id"] = int(values["account_id"]) if values["account_id"] else None
    return parsed, errors


def _form_values(
    name: str,
    direction: str,
    frequency: str,
    expected_amount: str,
    next_expected_date: str,
    account_id: str,
) -> dict[str, str]:
    return {
        "name": name,
        "direction": direction,
        "frequency": frequency,
        "expected_amount": expected_amount,
        "next_expected_date": next_expected_date,
        "account_id": account_id,
    }


@router.get("/new", include_in_schema=False)
async def new_form(
    request: Request,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    return await _editor(request, service, owner_user_id, None, _values(None), [])


@router.post("/new", include_in_schema=False)
async def create(
    request: Request,
    name: Annotated[str, Form()] = "",
    direction: Annotated[str, Form()] = "outflow",
    frequency: Annotated[str, Form()] = "monthly",
    expected_amount: Annotated[str, Form()] = "",
    next_expected_date: Annotated[str, Form()] = "",
    account_id: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    values = _form_values(
        name, direction, frequency, expected_amount, next_expected_date, account_id
    )
    parsed, errors = _parse(values)
    if errors:
        return await _editor(request, service, owner_user_id, None, values, errors, 422)
    stream = await service.create_recurring_stream(
        owner_user_id=owner_user_id, **parsed
    )
    await service.db.commit()
    response = Response(status_code=200)
    navigate(response, f"{SECTION.path}?{_tab_query(tab_of_row(stream), '')}")
    return close_dialog(with_toast(response, f"Added {stream.name}."))


def tab_of_row(stream: FinanceRecurringStream) -> str:
    return "income" if stream.direction == "inflow" else "bills"


@router.get("/{stream_id:int}/edit", include_in_schema=False)
async def edit_form(
    request: Request,
    stream_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    stream = await _stream(service, stream_id, owner_user_id)
    return await _editor(request, service, owner_user_id, stream, _values(stream), [])


@router.post("/{stream_id:int}/edit", include_in_schema=False)
async def edit(
    request: Request,
    stream_id: int,
    name: Annotated[str, Form()] = "",
    frequency: Annotated[str, Form()] = "monthly",
    expected_amount: Annotated[str, Form()] = "",
    next_expected_date: Annotated[str, Form()] = "",
    account_id: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    stream = await _stream(service, stream_id, owner_user_id)
    values = _form_values(
        name,
        stream.direction,
        frequency,
        expected_amount,
        next_expected_date,
        account_id,
    )
    parsed, errors = _parse(values)
    if errors:
        return await _editor(
            request, service, owner_user_id, stream, values, errors, 422
        )
    try:
        await service.update_recurring(
            stream_id,
            owner_user_id=owner_user_id,
            name=parsed["name"],
            frequency=parsed["frequency"],
            expected_amount=parsed["expected_amount"],
            next_expected_date=parsed["next_expected_date"],
            account_id=parsed["account_id"],
        )
    except ValueError as exc:  # the account already carries this bill
        return await _editor(
            request, service, owner_user_id, stream, values, [str(exc)], 422
        )
    response = await _row_response(request, service, stream, owner_user_id, oob=True)
    return close_dialog(with_toast(response, f"Saved {parsed['name']}."))


# --- match a payment, and the review queue --------------------------------


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
    if attached is None:
        raise HTTPException(status_code=404)
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
    if handler is None:
        raise HTTPException(status_code=404)
    await _stream(service, stream_id, owner_user_id)
    await handler(stream_id, service=service, owner_user_id=owner_user_id)  # type: ignore[call-arg]
    stream = await _stream(service, stream_id, owner_user_id)
    return await _row_response(request, service, stream, owner_user_id)
