"""Budget: the month header and its four tabs, rendered.

One page route (``?tab=``, ``?month=`` for the months ahead) renders the
stats strip, the month pager and the active tab from the API's summary,
outlook, suggestions, goals and envelopes. Every cell of the strip opens
its arithmetic in the dialog. Lines, suggestions, goals and envelopes
are row actions and dialog forms; each write answers with what changed
and re-sends the strip out of band, so the verdict never goes stale.
"""

from __future__ import annotations

import calendar
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from starlette.responses import Response

from app.components.backend.api.finance.budgets import (
    budget_outlook,
    budget_stat_details,
    budget_suggestions,
    budget_summary,
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
from app.components.web_frontend.filters import money, money_to_cents
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import (
    dialog,
    dialog_done,
    render,
    templates,
    with_toast,
)
from app.components.web_frontend.routes.finance.budget_display import (
    equation_rows,
    eta_caption,
    outlook_cells,
    stats_cells,
)
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.schemas import (
    BudgetLineUpsert,
    BudgetSuggestionIds,
    BudgetSummaryResponse,
    GoalParseRequest,
)
from app.services.finance.service import FinanceService

SECTION = section("budget")
router = APIRouter(prefix=SECTION.path)

TABS: tuple[tuple[str, str], ...] = (
    ("limits", "Limits"),
    ("suggested", "Suggested"),
    ("goals", "Goals"),
    ("envelopes", "Envelopes"),
)
OUTLOOK_MONTHS = 6
# The strip's cells, in order, and the dialog each opens.
STAT_KEYS = ("income", "bills", "budgets", "everything", "month")
COMMITMENT_BUCKETS = (
    ("fixed", "Monthly bills", "the same every month"),
    ("non_monthly", "Non-monthly bills", "at their monthly share"),
    ("one_time", "One-time", "this month only"),
)


# --- context --------------------------------------------------------------


async def _stats_context(
    service: FinanceService, owner_user_id: int | None, account_ids: list[int] | None
) -> tuple[BudgetSummaryResponse, dict[str, Any]]:
    summary = await budget_summary(
        month=None,
        account_ids=account_ids,
        service=service,
        owner_user_id=owner_user_id,
    )
    return summary, {"cells": stats_cells(summary.stats), "clickable": True}


async def budget_context(
    service: FinanceService,
    owner_user_id: int | None,
    tab: str,
    month: int,
    account_ids: list[int] | None,
) -> dict[str, Any]:
    """Everything ``components/budget.html`` renders."""
    summary, stats = await _stats_context(service, owner_user_id, account_ids)
    outlook = await budget_outlook(
        months=OUTLOOK_MONTHS,
        account_ids=account_ids,
        service=service,
        owner_user_id=owner_user_id,
    )
    month = max(0, min(month, len(outlook.items) - 1)) if outlook.items else 0
    if month > 0:
        stats = {"cells": outlook_cells(outlook.items[month]), "clickable": False}
    pager = [
        {
            "index": i,
            "label": (
                f"Now {money(e.start_balance, whole=True)}"
                if i == 0
                else f"{calendar.month_abbr[e.period_month % 100]} "
                f"{money(e.end_balance, whole=True)}"
            ),
            "tone": "error" if i and e.end_balance < 0 else None,
        }
        for i, e in enumerate(outlook.items)
    ]
    goals = await list_goals(service=service, owner_user_id=owner_user_id)
    envelopes = await list_envelopes(service=service, owner_user_id=owner_user_id)
    suggestions = await budget_suggestions(service=service, owner_user_id=owner_user_id)
    tab = tab if tab in dict(TABS) else TABS[0][0]
    counts = {
        "suggested": suggestions.total,
        "goals": goals.total,
        "envelopes": envelopes.total,
    }
    query = "".join(f"&account_ids={i}" for i in (account_ids or []))
    return {
        "path": SECTION.path,
        "tab": tab,
        "month": month,
        "query": query,
        "tabs": [
            (key, f"{label} ({counts[key]})" if counts.get(key) else label)
            for key, label in TABS
        ],
        "stats": stats,
        "pager": pager,
        "summary": summary,
        "buckets": {b.name: b for b in summary.buckets},
        "commitments": COMMITMENT_BUCKETS,
        "suggestions": suggestions,
        "goals": [(g, eta_caption(g)) for g in goals.items],
        "envelopes": envelopes.items,
        "accounts": (
            await service.list_accounts(owner_user_id=owner_user_id, page_size=500)
        )[0],
        "selected_ids": account_ids or [],
    }


async def _with_strip(
    request: Request,
    service: FinanceService,
    owner_user_id: int | None,
    template: str,
    context: dict[str, Any],
    status_code: int = 200,
) -> Response:
    """A write's answer: ``template`` as the primary content (may render
    nothing) plus the stats strip out of band."""
    await service.db.commit()
    _summary, stats = await _stats_context(service, owner_user_id, None)
    return templates.TemplateResponse(
        request=request,
        name=template,
        context={**context, "stats": stats, "strip_oob": True},
        status_code=status_code,
    )


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
    month: int = Query(default=0, ge=0),
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
    """The rows behind one cell. The verdict and Budgets come from the
    summary already on screen; the rest from the details endpoint."""
    if key not in STAT_KEYS:
        raise HTTPException(status_code=404)
    summary = await budget_summary(
        month=None,
        account_ids=account_ids,
        service=service,
        owner_user_id=owner_user_id,
    )
    footer = ""
    if key == "month":
        title, rows = "The month, line by line", equation_rows(summary.stats)
    elif key == "budgets":
        flexible = next((b for b in summary.buckets if b.name == "flexible"), None)
        rows = sorted(
            (
                {
                    "label": line.category_name or line.payee_label or "Overall",
                    "value": line.allocated_amount,
                    "caption": f"{money(line.spent_amount)} spent",
                }
                for line in (flexible.lines if flexible else [])
            ),
            key=lambda r: -r["value"],
        )
        title = "Limits you've set"
    else:
        details = await budget_stat_details(
            account_ids=account_ids, service=service, owner_user_id=owner_user_id
        )
        source = {
            "income": ("Confirmed income", details.income),
            "bills": ("Bills, monthly equivalent", details.bills),
            "everything": ("Everything else", details.everything_else),
        }[key]
        title = source[0]
        rows = [
            {"label": r.label, "value": r.value, "caption": r.frequency}
            for r in source[1]
        ]
        if key == "bills":
            footer = "Non-monthly bills shown at their monthly share"
        elif key == "everything":
            footer = "Spending no bill or limit covers"
    return dialog(
        request,
        "partials/budget/stat_details.html",
        title=title,
        rows=rows,
        footer=footer,
    )


# --- lines ------------------------------------------------------------------


def _line_response(
    request: Request, service: FinanceService, owner_user_id: int | None, line: Any
) -> Any:
    return _with_strip(
        request, service, owner_user_id, "partials/budget/line.html", {"line": line}
    )


@router.get("/lines/{line_id:int}/transactions", include_in_schema=False)
async def line_transactions(
    request: Request,
    line_id: int,
    account_ids: list[int] | None = Query(default=None),
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """The transactions behind one limit (pattern 4). "$535.16 of
    $1,000.00" was a figure with no way to ask what it was made of."""
    rows = await service.budget_line_transactions(
        line_id, owner_user_id=owner_user_id, account_ids=account_ids
    )
    summary = await budget_summary(
        month=None,
        account_ids=account_ids,
        service=service,
        owner_user_id=owner_user_id,
    )
    # The FLEXIBLE bucket only. A commitment line's ``id`` is its
    # recurring stream's, not a budget line's, so searching every bucket
    # matches the wrong row on a collision - which it promptly did.
    line = next(
        (
            item
            for bucket in summary.buckets
            if bucket.name == "flexible"
            for item in bucket.lines
            if item.id == line_id
        ),
        None,
    )
    if line is None:
        raise HTTPException(status_code=404)
    return dialog(
        request,
        "partials/transactions_dialog.html",
        title=line.category_name or line.payee_label or "Limit",
        subtitle=(
            f"{money(line.spent_amount)} of {money(line.allocated_amount)} this month"
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
    return dialog(
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
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Set a limit. From a row (the inline amount) the answer is the row;
    from a dialog or the goal parser it is a navigation back to the page."""
    cents = money_to_cents(allocated_amount)
    if cents is None or cents <= 0:
        if source == "dialog":
            categories = await list_category_options(service=service)
            return dialog(
                request,
                "partials/budget/line_new.html",
                422,
                categories=categories.items,
                errors=["Enter the monthly limit in dollars."],
            )
        raise HTTPException(
            status_code=422, detail="Enter the monthly limit in dollars."
        )
    line = await upsert_budget_line(
        BudgetLineUpsert(
            category_id=int(category_id) if category_id else None,
            payee_key=payee_key or None,
            payee_label=payee_label or None,
            allocated_amount=cents,
        ),
        month=None,
        service=service,
        owner_user_id=owner_user_id,
    )
    if source == "row":
        return await _line_response(request, service, owner_user_id, line)
    await service.db.commit()
    label = line.category_name or line.payee_label or "Overall"
    return dialog_done(SECTION.path, f"Limit set for {label}.")


@router.delete("/lines/{line_id:int}", include_in_schema=False)
async def remove_line(
    request: Request,
    line_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    if not await service.delete_budget_line(line_id, owner_user_id=owner_user_id):
        raise HTTPException(status_code=404)
    return await _with_strip(
        request, service, owner_user_id, "partials/budget/stats.html", {}
    )


# --- suggestions ---------------------------------------------------------------


async def _suggestions_response(
    request: Request, service: FinanceService, owner_user_id: int | None
) -> Response:
    await service.db.commit()
    suggestions = await budget_suggestions(service=service, owner_user_id=owner_user_id)
    return await _with_strip(
        request,
        service,
        owner_user_id,
        "partials/budget/suggestions.html",
        {"suggestions": suggestions, "path": SECTION.path},
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
    cents = money_to_cents(amount)
    if cents is None or cents <= 0:
        raise HTTPException(
            status_code=422, detail="Enter the monthly limit in dollars."
        )
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
        return dialog(
            request,
            "partials/budget/goal_parse.html",
            422,
            result=None,
            errors=["Couldn't find a category or recent payee matching that."],
        )
    return dialog(request, "partials/budget/goal_parse.html", result=result, errors=[])
