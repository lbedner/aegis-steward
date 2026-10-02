"""Budget: the month header and its four tabs, rendered.

One page route (``?tab=``, ``?month=`` for the months ahead) renders the
stats strip, the month pager and the active tab from the API's summary,
outlook, suggestions, goals and envelopes. Every cell of the strip opens
its arithmetic in the dialog. Lines, suggestions, goals and envelopes
are row actions and dialog forms; each write answers with what changed
and re-sends the strip out of band, so the verdict never goes stale.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from starlette.responses import Response

from app.components.backend.api.finance.budgets import (
    budget_actuals,
    budget_outlook,
    budget_stat_details,
    budget_suggestions,
    dismiss_budget_suggestions,
    parse_budget_goal,
    restore_budget_suggestions,
    upsert_budget_line,
)
from app.components.backend.api.finance.categories import list_category_options
from app.components.backend.api.finance.goals import (
    list_goals,
)
from app.components.backend.api.finance.planning import (
    list_envelopes,
)
from app.components.backend.api.finance.register import hydrate_transactions
from app.components.web_frontend.filters import account_params, money
from app.components.web_frontend.rendering import (
    dialog,
    dialog_done,
    or_404,
    render,
    with_toast,
)
from app.components.web_frontend.routes.finance.budget_display import (
    SECTION,
    budget_dialog,
    eta_caption,
    remove_dialog,
    removed,
    stats_context,
    with_strip,
)
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.domains.planning.budgets import strip
from app.services.finance.schemas import (
    BudgetLineUpsert,
    BudgetSuggestionIds,
    GoalParseRequest,
    PeriodMonth,
)
from app.services.finance.service import FinanceService
from app.services.finance.utils import (
    current_period_month,
    period_label,
    positive_cents,
    shift_period,
)

router = APIRouter(prefix=SECTION.path)

_LIMIT_IN_DOLLARS = "Enter the monthly limit in dollars."

TABS: tuple[tuple[str, str], ...] = (
    ("limits", "Limits"),
    ("suggested", "Suggested"),
    ("goals", "Goals"),
    ("envelopes", "Envelopes"),
)
OUTLOOK_MONTHS = 6
# How far back the pager reaches: months that have ended, read as they went.
PAST_MONTHS = 6


# --- context --------------------------------------------------------------


async def budget_context(
    service: FinanceService,
    owner_user_id: int | None,
    tab: str,
    month: int,
    account_ids: list[int] | None,
) -> dict[str, Any]:
    """Everything ``components/budget.html`` renders. ``month`` counts from
    this one: ahead is the outlook, behind is a month that has ended."""
    outlook = await budget_outlook(
        months=OUTLOOK_MONTHS,
        account_ids=account_ids,
        service=service,
        owner_user_id=owner_user_id,
    )
    month = max(-PAST_MONTHS, min(month, len(outlook.items) - 1))
    current = current_period_month()
    period = shift_period(current, month) if month < 0 else None
    summary, stats = await stats_context(service, owner_user_id, account_ids, period)
    if period is not None:
        actuals = await budget_actuals(
            month=period,
            account_ids=account_ids,
            service=service,
            owner_user_id=owner_user_id,
        )
        stats = {
            "cells": strip.review_cells(summary.stats, actuals),
            "clickable": False,
        }
    elif month > 0:
        stats = {"cells": strip.outlook_cells(outlook.items[month]), "clickable": False}
    tab = tab if tab in dict(TABS) else TABS[0][0]

    def url(at_month: int, at_tab: str) -> str:
        """One URL per link: its href and its swap are the same request."""
        params = [f"month={at_month}", f"tab={at_tab}", *account_params(account_ids)]
        return f"{SECTION.path}?{'&'.join(params)}"

    past = [shift_period(current, -back) for back in range(PAST_MONTHS, 0, -1)]
    pager = [
        {
            "index": i - PAST_MONTHS,
            "label": c.label,
            "tone": c.tone,
            "url": url(i - PAST_MONTHS, tab),
        }
        for i, c in enumerate(strip.pager_chips(outlook.items, past))
    ]
    at = month + PAST_MONTHS
    goals = await list_goals(service=service, owner_user_id=owner_user_id)
    envelopes = await list_envelopes(service=service, owner_user_id=owner_user_id)
    suggestions = await budget_suggestions(service=service, owner_user_id=owner_user_id)
    counts = {
        "suggested": suggestions.total,
        "goals": goals.total,
        "envelopes": envelopes.total,
    }
    return {
        "path": SECTION.path,
        "tab": tab,
        "month": month,
        "period": period,
        "heading": (
            f"How did {period_label(period)} go?" if period else "Does the month work?"
        ),
        "prev_url": pager[at - 1]["url"] if at > 0 else None,
        "next_url": pager[at + 1]["url"] if at + 1 < len(pager) else None,
        "tabs": [
            (
                key,
                f"{label} ({counts[key]})" if counts.get(key) else label,
                url(month, key),
            )
            for key, label in TABS
        ],
        "stats": stats,
        "pager": pager,
        "summary": summary,
        "commitments": strip.COMMITMENT_BUCKETS,
        "commitments_line": strip.commitments_line(summary),
        "suggestions": suggestions,
        "goals": [(g, eta_caption(g)) for g in goals.items],
        "envelopes": envelopes.items,
        "accounts": (
            await service.list_accounts(owner_user_id=owner_user_id, page_size=500)
        )[0],
        "selected_ids": account_ids or [],
    }


# --- the page, the strip, the details ----------------------------------------


# What a drill-down row shows. The same four the Overview's slice dialog
# uses: a transaction reads the same way wherever it is listed.
LINE_TXN_COLUMNS = [
    {"key": "date", "label": "Date", "kind": "date"},
    {"key": "payee", "label": "Payee", "kind": "avatar"},
    {"key": "category", "label": "Category"},
    {"key": "amount", "label": "Amount", "kind": "money", "align": "right"},
]


@router.get("", include_in_schema=False)
async def page(
    request: Request,
    tab: str = "limits",
    month: int = Query(default=0, ge=-PAST_MONTHS),
    account_ids: list[int] | None = Query(default=None),
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    context = await budget_context(service, owner_user_id, tab, month, account_ids)
    return render(request, "pages/budget.html", {"section": SECTION, **context})


@router.get("/stats/{key}", include_in_schema=False)
async def stat_details(
    request: Request,
    key: str,
    account_ids: list[int] | None = Query(default=None),
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """The rows behind one cell, as ``strip`` words them."""
    if key not in strip.STAT_KEYS:
        raise HTTPException(status_code=404)
    summary, _stats = await stats_context(service, owner_user_id, account_ids)
    details = (
        await budget_stat_details(
            account_ids=account_ids, service=service, owner_user_id=owner_user_id
        )
        if key in strip.DETAIL_KEYS
        else None
    )
    popup = strip.stat_popup(key, summary, details)
    return dialog(
        request,
        "partials/budget/stat_details.html",
        title=popup.title,
        rows=popup.rows,
        footer=popup.footer,
    )


# --- lines ------------------------------------------------------------------


def _line_response(
    request: Request, service: FinanceService, owner_user_id: int | None, line: Any
) -> Any:
    return with_strip(
        request, service, owner_user_id, "partials/budget/line.html", {"line": line}
    )


@router.get("/lines/{line_id:int}/transactions", include_in_schema=False)
async def line_transactions(
    request: Request,
    line_id: int,
    month: Annotated[PeriodMonth | None, Query()] = None,
    account_ids: list[int] | None = Query(default=None),
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """The transactions behind one limit (pattern 4), this month's or, from
    a month that has ended, that month's. "$535.16 of $1,000.00" was a
    figure with no way to ask what it was made of."""
    rows = await service.budget_line_transactions(
        line_id,
        owner_user_id=owner_user_id,
        period_month=month,
        account_ids=account_ids,
    )
    summary, _stats = await stats_context(service, owner_user_id, account_ids, month)
    # The FLEXIBLE bucket only. A commitment line's ``id`` is its
    # recurring stream's, not a budget line's, so searching every bucket
    # matches the wrong row on a collision - which it promptly did.
    line = next(
        (item for item in summary.bucket("flexible").lines if item.id == line_id),
        None,
    )
    or_404(line)
    return dialog(
        request,
        "partials/transactions_dialog.html",
        title=line.label,
        subtitle=(
            f"{money(line.spent_amount)} of {money(line.available_amount)} "
            + (f"in {period_label(month)}" if month else "this month")
        ),
        rows=await hydrate_transactions(service, rows),
        columns=LINE_TXN_COLUMNS,
        empty="Nothing has been spent against this limit yet",
    )


@router.get("/lines/new", include_in_schema=False)
async def new_line_form(
    request: Request,
    service: FinanceService = Depends(get_finance_service),
) -> Response:
    categories = await list_category_options(service=service)
    return budget_dialog(
        request, "partials/budget/line_new.html", categories=categories.items, errors=[]
    )


@router.post("/lines", include_in_schema=False)
async def upsert_line(
    request: Request,
    allocated_amount: Annotated[str, Form()] = "",
    category_id: Annotated[str, Form()] = "",
    payee_key: Annotated[str, Form()] = "",
    payee_label: Annotated[str, Form()] = "",
    source: Annotated[str, Form()] = "row",
    rollover: Annotated[str, Form()] = "",
    rollover_sent: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Set a limit. From a row (the inline amount) the answer is the row;
    from a dialog or the goal parser it is a navigation back to the page.
    Only the row's own form says rollover (``rollover_sent``); every other
    form leaves it as it was."""
    cents = positive_cents(allocated_amount)
    if cents is None:
        if source == "dialog":
            categories = await list_category_options(service=service)
            return budget_dialog(
                request,
                "partials/budget/line_new.html",
                422,
                categories=categories.items,
                errors=[_LIMIT_IN_DOLLARS],
            )
        raise HTTPException(status_code=422, detail=_LIMIT_IN_DOLLARS)
    line = await upsert_budget_line(
        BudgetLineUpsert(
            category_id=int(category_id) if category_id else None,
            payee_key=payee_key or None,
            payee_label=payee_label or None,
            allocated_amount=cents,
            rollover_enabled=(rollover == "on") if rollover_sent else None,
        ),
        month=None,
        service=service,
        owner_user_id=owner_user_id,
    )
    if source == "row":
        return await _line_response(request, service, owner_user_id, line)
    await service.db.commit()
    return dialog_done(SECTION.path, f"Limit set for {line.label}.")


@router.get("/lines/{line_id:int}/remove", include_in_schema=False)
async def remove_line_form(request: Request, line_id: int) -> Response:
    return remove_dialog(
        request,
        title="Remove this limit?",
        body="The month stays decided: last month's limit is not copied back in.",
        url=f"{SECTION.path}/lines/{line_id}",
    )


@router.delete("/lines/{line_id:int}", include_in_schema=False)
async def remove_line(
    request: Request,
    line_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    if not await service.delete_budget_line(line_id, owner_user_id=owner_user_id):
        raise HTTPException(status_code=404)
    return await removed(
        request, service, owner_user_id, f"line-{line_id}", "Limit removed."
    )


# --- suggestions ---------------------------------------------------------------


async def _suggestions_response(
    request: Request, service: FinanceService, owner_user_id: int | None
) -> Response:
    await service.db.commit()
    suggestions = await budget_suggestions(service=service, owner_user_id=owner_user_id)
    return await with_strip(
        request,
        service,
        owner_user_id,
        "partials/budget/suggestions.html",
        {"suggestions": suggestions},
    )


@router.post("/suggestions/{category_id:int}/dismiss", include_in_schema=False)
async def dismiss_suggestion(
    request: Request,
    category_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    await dismiss_budget_suggestions(
        BudgetSuggestionIds(category_ids=[category_id]),
        service=service,
        owner_user_id=owner_user_id,
    )
    return await _suggestions_response(request, service, owner_user_id)


@router.post("/suggestions/{category_id:int}/restore", include_in_schema=False)
async def restore_suggestion(
    request: Request,
    category_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    await restore_budget_suggestions(
        BudgetSuggestionIds(category_ids=[category_id]),
        service=service,
        owner_user_id=owner_user_id,
    )
    return await _suggestions_response(request, service, owner_user_id)


@router.post("/suggestions/{category_id:int}/accept", include_in_schema=False)
async def accept_suggestion(
    request: Request,
    category_id: int,
    amount: Annotated[str, Form()],
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """The suggested median becomes a limit; the card goes with it."""
    cents = positive_cents(amount)
    if cents is None:
        raise HTTPException(status_code=422, detail=_LIMIT_IN_DOLLARS)
    await upsert_budget_line(
        BudgetLineUpsert(category_id=category_id, allocated_amount=cents),
        month=None,
        service=service,
        owner_user_id=owner_user_id,
    )
    response = await _suggestions_response(request, service, owner_user_id)
    return with_toast(response, "Limit set.")


@router.post("/goal", include_in_schema=False)
async def goal_parse(
    request: Request,
    text: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """A natural-language goal previewed as a limit; Confirm posts the
    upsert with the parsed target."""
    result = await parse_budget_goal(
        GoalParseRequest(text=text), service=service, owner_user_id=owner_user_id
    )
    if not result.matched:
        return budget_dialog(
            request,
            "partials/budget/goal_parse.html",
            422,
            result=None,
            errors=["Couldn't find a category or recent payee matching that."],
        )
    return budget_dialog(
        request, "partials/budget/goal_parse.html", result=result, errors=[]
    )
