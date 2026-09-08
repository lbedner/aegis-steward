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
from datetime import date
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
    create_goal,
    goal_response,
    list_goals,
    preview_goal_target,
    update_goal,
)
from app.components.backend.api.finance.planning import (
    create_envelope,
    envelope_response,
    list_envelopes,
    update_envelope,
)
from app.components.web_frontend.filters import cents_to_input, money_to_cents
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import (
    close_dialog,
    dialog,
    dialog_done,
    render,
    templates,
    with_toast,
)
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.domains.planning.envelopes import envelope_metadata
from app.services.finance.domains.planning.goals import goal_metadata
from app.services.finance.models import FinanceAccount
from app.services.finance.schemas import (
    BudgetLineUpsert,
    BudgetStatsResponse,
    BudgetSuggestionIds,
    BudgetSummaryResponse,
    EnvelopeCreate,
    EnvelopeUpdate,
    GoalCreate,
    GoalParseRequest,
    GoalResponse,
    GoalUpdate,
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
TARGET_RULES = [
    {"id": "fixed", "name": "A fixed amount"},
    {"id": "months_of_expenses", "name": "Months of expenses"},
]
CONTRIBUTION_KINDS = [
    {"id": "fixed", "name": "A fixed amount each month"},
    {"id": "percent_income", "name": "A percent of income"},
    {"id": "surplus", "name": "Whatever the month leaves over"},
]
CADENCES = [{"id": "weekly", "name": "Weekly"}, {"id": "monthly", "name": "Monthly"}]
COMMITMENT_BUCKETS = (
    ("fixed", "Monthly bills", "the same every month"),
    ("non_monthly", "Non-monthly bills", "at their monthly share"),
    ("one_time", "One-time", "this month only"),
)


# --- pure presentation ----------------------------------------------------


def month_label(period_month: int) -> str:
    """``202610`` -> ``October 2026``."""
    return f"{calendar.month_name[period_month % 100]} {period_month // 100}"


def _cell(
    key: str, label: str, value: int, caption: str, tone: str | None = None
) -> dict:
    return {
        "key": key,
        "label": label,
        "value": value,
        "caption": caption,
        "tone": tone,
    }


def stats_cells(stats: BudgetStatsResponse) -> list[dict[str, Any]]:
    """The header strip for the current month: what comes in, what the
    bills take, what the limits take, and the signed remainder. Colour
    only for a month in trouble."""
    plural = "s" if stats.income_count != 1 else ""
    budgets_caption = f"{stats.flexible_count} limits"
    if stats.goals_total > 0:
        budgets_caption += " · + goals"
    cells = [
        _cell(
            "income",
            "Income",
            stats.income_total,
            f"{stats.income_count} confirmed source{plural} / month",
        ),
        _cell(
            "bills", "Bills", stats.fixed_total, f"{stats.fixed_count} bills / month"
        ),
        _cell("budgets", "Budgets", stats.flexible_allocated, budgets_caption),
    ]
    if stats.everything_else > 0:
        cells.append(
            _cell(
                "everything",
                "Everything else",
                stats.everything_else,
                "observed · not in bills or limits",
            )
        )
    if stats.month_net >= 0:
        caption, tone = "Left over at these settings", "ok"
    else:
        caption, tone = (
            f"Short this month · {stats.days_left_in_period} days left",
            "error",
        )
    cells.append(_cell("month", "This month", stats.month_net, caption, tone))
    return cells


def outlook_cells(entry: Any) -> list[dict[str, Any]]:
    """The strip for a FUTURE month: bills at face value on their real
    cadence, and the verdict titled with the month itself."""
    cells = [
        _cell("income", "Income", entry.income_due, "due that month"),
        _cell("bills", "Bills", entry.bills_due, "landing that month, face value"),
        _cell("budgets", "Budgets", entry.budgets, "standing limits"),
    ]
    if entry.everything_else > 0:
        cells.append(
            _cell(
                "everything",
                "Everything else",
                entry.everything_else,
                "observed · not in bills or limits",
            )
        )
    cells.append(
        _cell(
            "month",
            month_label(entry.period_month),
            entry.month_net,
            "at these settings",
            "ok" if entry.month_net >= 0 else "error",
        )
    )
    return cells


def equation_rows(stats: BudgetStatsResponse) -> list[dict[str, Any]]:
    """The verdict as its own arithmetic, from the same stats the strip
    renders; zero terms stay out."""
    rows = [
        {"label": "Income", "value": stats.income_total},
        {"label": "Bills", "value": -stats.fixed_total},
        {"label": "Budgets", "value": -stats.flexible_allocated},
    ]
    for label, amount in (
        ("Goals", stats.goals_total),
        ("Envelopes", stats.envelopes_total),
        ("Everything else", stats.everything_else),
    ):
        if amount:
            rows.append({"label": label, "value": -amount})
    rows.append({"label": "This month", "value": stats.month_net})
    return rows


def eta_caption(goal: GoalResponse) -> str:
    if goal.status == "reached" or goal.progress >= 1:
        return "Reached"
    if goal.status == "paused":
        return "Paused"
    monthly = f"{goal.monthly_need / 100:,.2f}"
    if goal.contribution_kind == "percent_income":
        monthly += f" ({(goal.contribution_pct_bps or 0) / 100:g}% of income)"
    elif goal.contribution_kind == "surplus":
        monthly += " (surplus)"
    if goal.eta is None:
        return f"${monthly}/mo · at this rate: never"
    return f"${monthly}/mo · lands {goal.eta:%b %d, %Y}"


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
                f"Now ${round(e.start_balance / 100):,}"
                if i == 0
                else f"{calendar.month_abbr[e.period_month % 100]} "
                f"{'-' if e.end_balance < 0 else ''}${abs(round(e.end_balance / 100)):,}"
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
                    "caption": f"{line.spent_amount / 100:,.2f} spent",
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


# --- goals -----------------------------------------------------------------------


async def _goal(
    service: FinanceService, account_id: int, owner_user_id: int | None
) -> FinanceAccount:
    account = await service.get_account(account_id, owner_user_id=owner_user_id)
    if account is None or goal_metadata(account.metadata_) is None:
        raise HTTPException(status_code=404)
    return account


async def _goal_card(
    request: Request,
    service: FinanceService,
    owner_user_id: int | None,
    account: FinanceAccount,
    oob: bool = False,
) -> Response:
    await service.db.commit()
    goal = await goal_response(service, account)
    return dialog(
        request,
        "partials/budget/goal_card.html",
        goal=goal,
        eta=eta_caption(goal),
        oob=oob,
        path=SECTION.path,
    )


def _goal_values(goal: GoalResponse | None) -> dict[str, str]:
    if goal is None:
        return {
            "name": "",
            "target_rule": "fixed",
            "target_amount": "",
            "target_factor": "",
            "target_date": "",
            "contribution_kind": "fixed",
            "monthly_contribution": "",
            "contribution_pct": "",
            "auto_contribute": "",
        }
    return {
        "name": goal.name,
        "target_rule": goal.target_rule,
        "target_amount": cents_to_input(goal.target_amount),
        "target_factor": str(goal.target_factor or ""),
        "target_date": goal.target_date.isoformat() if goal.target_date else "",
        "contribution_kind": goal.contribution_kind,
        "monthly_contribution": cents_to_input(goal.monthly_contribution),
        "contribution_pct": (
            f"{goal.contribution_pct_bps / 100:g}" if goal.contribution_pct_bps else ""
        ),
        "auto_contribute": "on" if goal.auto_contribute else "",
    }


async def _goal_editor(
    request: Request,
    service: FinanceService,
    owner_user_id: int | None,
    goal: GoalResponse | None,
    values: dict[str, str],
    errors: list[str],
    status_code: int = 200,
) -> Response:
    accounts, _total = await service.list_accounts(
        owner_user_id=owner_user_id, page_size=500
    )
    return dialog(
        request,
        "partials/budget/goal_editor.html",
        status_code,
        goal=goal,
        values=values,
        errors=errors,
        rules=TARGET_RULES,
        kinds=CONTRIBUTION_KINDS,
        accounts=accounts,
        scope=list(goal.target_scope) if goal else [],
        path=SECTION.path,
    )


def _goal_form(
    name: str,
    target_rule: str,
    target_amount: str,
    target_factor: str,
    target_date: str,
    contribution_kind: str,
    monthly_contribution: str,
    contribution_pct: str,
    auto_contribute: str,
) -> dict[str, str]:
    return {
        "name": name,
        "target_rule": target_rule,
        "target_amount": target_amount,
        "target_factor": target_factor,
        "target_date": target_date,
        "contribution_kind": contribution_kind,
        "monthly_contribution": monthly_contribution,
        "contribution_pct": contribution_pct,
        "auto_contribute": auto_contribute,
    }


def _parse_goal(
    values: dict[str, str], scope: list[int]
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    parsed: dict[str, Any] = {"name": values["name"].strip() or None}
    rule = values["target_rule"]
    if rule not in {r["id"] for r in TARGET_RULES}:
        errors.append("Pick how the target is sized.")
    parsed["target_rule"] = rule
    if rule == "fixed":
        cents = money_to_cents(values["target_amount"])
        if cents is None or cents <= 0:
            errors.append("Enter the target in dollars.")
        parsed["target_amount"] = cents
        parsed["target_factor"] = None
        parsed["target_scope"] = []
    else:
        try:
            parsed["target_factor"] = int(values["target_factor"])
        except ValueError:
            errors.append("Enter how many months of expenses.")
        parsed["target_amount"] = None
        parsed["target_scope"] = scope
    parsed["target_date"] = (
        date.fromisoformat(values["target_date"]) if values["target_date"] else None
    )
    kind = values["contribution_kind"]
    if kind not in {k["id"] for k in CONTRIBUTION_KINDS}:
        errors.append("Pick how to contribute.")
    parsed["contribution_kind"] = kind
    parsed["monthly_contribution"] = None
    parsed["contribution_pct_bps"] = None
    if kind == "fixed" and values["monthly_contribution"].strip():
        monthly = money_to_cents(values["monthly_contribution"])
        if monthly is None:
            errors.append("Enter the monthly amount in dollars.")
        parsed["monthly_contribution"] = monthly
    if kind == "percent_income":
        try:
            parsed["contribution_pct_bps"] = int(
                float(values["contribution_pct"]) * 100
            )
        except ValueError:
            errors.append("Enter the percent of income.")
    parsed["auto_contribute"] = values["auto_contribute"] == "on"
    return parsed, errors


@router.get("/goals/preview", include_in_schema=False)
async def goal_preview(
    request: Request,
    target_rule: str = "fixed",
    target_factor: str = "",
    scope: list[int] | None = Query(default=None),
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """What the rule resolves to, from the same function that will save it."""
    text = ""
    if target_rule == "months_of_expenses" and target_factor.strip().isdigit():
        factor = int(target_factor)
        preview = await preview_goal_target(
            factor=factor,
            rule="months_of_expenses",
            scope=scope,
            service=service,
            owner_user_id=owner_user_id,
        )
        text = (
            f"= ${preview.target_amount / 100:,.2f} "
            f"({factor} months × ${preview.expenses / 100:,.2f} of monthly expenses)"
        )
    return dialog(request, "partials/budget/target_preview.html", text=text)


@router.get("/goals/new", include_in_schema=False)
async def new_goal_form(
    request: Request,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    return await _goal_editor(
        request, service, owner_user_id, None, _goal_values(None), []
    )


@router.post("/goals/new", include_in_schema=False)
async def create_goal_route(
    request: Request,
    name: Annotated[str, Form()] = "",
    target_rule: Annotated[str, Form()] = "fixed",
    target_amount: Annotated[str, Form()] = "",
    target_factor: Annotated[str, Form()] = "",
    target_date: Annotated[str, Form()] = "",
    contribution_kind: Annotated[str, Form()] = "fixed",
    monthly_contribution: Annotated[str, Form()] = "",
    contribution_pct: Annotated[str, Form()] = "",
    auto_contribute: Annotated[str, Form()] = "",
    scope: Annotated[list[int], Form()] = [],
    linked_account_id: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    values = _goal_form(
        name,
        target_rule,
        target_amount,
        target_factor,
        target_date,
        contribution_kind,
        monthly_contribution,
        contribution_pct,
        auto_contribute,
    )
    parsed, errors = _parse_goal(values, scope)
    if not linked_account_id and not parsed["name"]:
        errors.insert(0, "Give the goal a name.")
    if errors:
        return await _goal_editor(
            request, service, owner_user_id, None, values, errors, 422
        )
    if linked_account_id:
        parsed["name"] = None
        parsed["account_id"] = int(linked_account_id)
    try:
        goal = await create_goal(
            GoalCreate(**parsed), service=service, owner_user_id=owner_user_id
        )
    except HTTPException as exc:
        return await _goal_editor(
            request, service, owner_user_id, None, values, [str(exc.detail)], 422
        )
    await service.db.commit()
    return dialog_done(f"{SECTION.path}?tab=goals", f"Added {goal.name}.")


@router.get("/goals/{account_id:int}/edit", include_in_schema=False)
async def edit_goal_form(
    request: Request,
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    goal = await goal_response(service, await _goal(service, account_id, owner_user_id))
    return await _goal_editor(
        request, service, owner_user_id, goal, _goal_values(goal), []
    )


@router.post("/goals/{account_id:int}/edit", include_in_schema=False)
async def edit_goal(
    request: Request,
    account_id: int,
    name: Annotated[str, Form()] = "",
    target_rule: Annotated[str, Form()] = "fixed",
    target_amount: Annotated[str, Form()] = "",
    target_factor: Annotated[str, Form()] = "",
    target_date: Annotated[str, Form()] = "",
    contribution_kind: Annotated[str, Form()] = "fixed",
    monthly_contribution: Annotated[str, Form()] = "",
    contribution_pct: Annotated[str, Form()] = "",
    auto_contribute: Annotated[str, Form()] = "",
    scope: Annotated[list[int], Form()] = [],
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _goal(service, account_id, owner_user_id)
    goal = await goal_response(service, account)
    values = _goal_form(
        name,
        target_rule,
        target_amount,
        target_factor,
        target_date,
        contribution_kind,
        monthly_contribution,
        contribution_pct,
        auto_contribute,
    )
    parsed, errors = _parse_goal(values, scope)
    if errors:
        return await _goal_editor(
            request, service, owner_user_id, goal, values, errors, 422
        )
    parsed.pop("name")
    await update_goal(
        account_id, GoalUpdate(**parsed), service=service, owner_user_id=owner_user_id
    )
    if values["name"].strip() and values["name"].strip() != account.name:
        account.name = values["name"].strip()
        service.db.add(account)
    response = await _goal_card(request, service, owner_user_id, account, oob=True)
    return close_dialog(with_toast(response, f"Saved {account.name}."))


@router.post("/goals/{account_id:int}/pause", include_in_schema=False)
async def toggle_goal_pause(
    request: Request,
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _goal(service, account_id, owner_user_id)
    meta = goal_metadata(account.metadata_)
    assert meta is not None
    status = "active" if meta.status == "paused" else "paused"
    await service.set_goal_status(account_id, status, owner_user_id=owner_user_id)
    return await _goal_card(request, service, owner_user_id, account)


@router.get("/goals/{account_id:int}/contribute", include_in_schema=False)
async def contribute_form(
    request: Request,
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _goal(service, account_id, owner_user_id)
    return dialog(
        request,
        "partials/budget/move.html",
        title=f"Add to {account.name}",
        action=f"{SECTION.path}/goals/{account_id}/contribute",
        label="Add",
        note=False,
        errors=[],
    )


@router.post("/goals/{account_id:int}/contribute", include_in_schema=False)
async def contribute(
    request: Request,
    account_id: int,
    amount: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _goal(service, account_id, owner_user_id)
    cents = money_to_cents(amount)
    if cents is None or cents <= 0:
        return dialog(
            request,
            "partials/budget/move.html",
            422,
            title=f"Add to {account.name}",
            action=f"{SECTION.path}/goals/{account_id}/contribute",
            label="Add",
            note=False,
            errors=["Enter an amount in dollars."],
        )
    try:
        await service.contribute_to_goal(
            account_id, amount=cents, owner_user_id=owner_user_id
        )
    except ValueError as exc:  # a linked goal: its contributions are real transfers
        return dialog(
            request,
            "partials/budget/move.html",
            422,
            title=f"Add to {account.name}",
            action=f"{SECTION.path}/goals/{account_id}/contribute",
            label="Add",
            note=False,
            errors=[str(exc)],
        )
    response = await _goal_card(request, service, owner_user_id, account, oob=True)
    return close_dialog(
        with_toast(response, f"Added ${cents / 100:,.2f} to {account.name}.")
    )


@router.get("/goals/{account_id:int}/remove", include_in_schema=False)
async def remove_goal_form(
    request: Request,
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _goal(service, account_id, owner_user_id)
    return dialog(
        request,
        "partials/budget/remove.html",
        title=f"Remove {account.name}?",
        body="A virtual goal is deleted; a linked account only stops being a goal.",
        url=f"{SECTION.path}/goals/{account_id}",
    )


@router.delete("/goals/{account_id:int}", include_in_schema=False)
async def remove_goal(
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    from app.components.backend.api.finance.goals import delete_goal

    account = await _goal(service, account_id, owner_user_id)
    await delete_goal(account_id, service=service, owner_user_id=owner_user_id)
    await service.db.commit()
    return close_dialog(
        with_toast(Response(status_code=200), f"Removed {account.name}.")
    )


# --- envelopes ---------------------------------------------------------------------


async def _envelope(
    service: FinanceService, account_id: int, owner_user_id: int | None
) -> FinanceAccount:
    account = await service.get_account(account_id, owner_user_id=owner_user_id)
    if account is None or envelope_metadata(account.metadata_) is None:
        raise HTTPException(status_code=404)
    return account


async def _envelope_card(
    request: Request,
    service: FinanceService,
    account: FinanceAccount,
    oob: bool = False,
) -> Response:
    await service.db.commit()
    await service.db.refresh(account)
    return dialog(
        request,
        "partials/budget/envelope_card.html",
        envelope=envelope_response(account),
        oob=oob,
        path=SECTION.path,
    )


def _move_dialog(
    request: Request,
    account: FinanceAccount,
    verb: str,
    errors: list[str],
    status_code: int = 200,
) -> Response:
    return dialog(
        request,
        "partials/budget/move.html",
        status_code,
        title=f"{verb.title()} {account.name}",
        action=f"{SECTION.path}/envelopes/{account.id}/{verb}",
        label=verb.title(),
        note=True,
        errors=errors,
    )


@router.post("/envelopes/{account_id:int}/edit", include_in_schema=False)
async def edit_envelope(
    request: Request,
    account_id: int,
    monthly_credit: Annotated[str, Form()] = "",
    cadence: Annotated[str, Form()] = "monthly",
    auto_credit: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _envelope(service, account_id, owner_user_id)
    values = {
        **_envelope_values(account),
        "monthly_credit": monthly_credit,
        "cadence": cadence,
        "auto_credit": auto_credit,
    }
    credit = money_to_cents(monthly_credit)
    if credit is None or cadence not in {c["id"] for c in CADENCES}:
        return _envelope_editor(
            request,
            account,
            values,
            ["Amounts are in dollars; pick weekly or monthly."],
            422,
        )
    await update_envelope(
        account_id,
        EnvelopeUpdate(
            monthly_credit=credit or None,
            auto_credit=auto_credit == "on",
            cadence=cadence,
        ),  # type: ignore[arg-type]
        service=service,
        owner_user_id=owner_user_id,
    )
    response = await _envelope_card(request, service, account, oob=True)
    return close_dialog(with_toast(response, f"Saved {account.name}."))


@router.get("/envelopes/{account_id:int}/{verb}", include_in_schema=False)
async def envelope_move_form(
    request: Request,
    account_id: int,
    verb: str,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _envelope(service, account_id, owner_user_id)
    if verb == "edit":
        return _envelope_editor(request, account, _envelope_values(account), [])
    if verb not in ("credit", "spend"):
        raise HTTPException(status_code=404)
    return _move_dialog(request, account, verb, [])


@router.post("/envelopes/{account_id:int}/{verb}", include_in_schema=False)
async def envelope_move(
    request: Request,
    account_id: int,
    verb: str,
    amount: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _envelope(service, account_id, owner_user_id)
    if verb not in ("credit", "spend"):
        raise HTTPException(status_code=404)
    cents = money_to_cents(amount)
    if cents is None or cents <= 0:
        return _move_dialog(
            request, account, verb, ["Enter an amount in dollars."], 422
        )
    move = service.credit_envelope if verb == "credit" else service.spend_from_envelope
    await move(account_id, amount=cents, owner_user_id=owner_user_id, note=note or None)
    response = await _envelope_card(request, service, account, oob=True)
    return close_dialog(with_toast(response, f"{verb.title()}: ${cents / 100:,.2f}."))


def _envelope_values(account: FinanceAccount | None) -> dict[str, str]:
    if account is None:
        return {
            "name": "",
            "monthly_credit": "",
            "cadence": "monthly",
            "starting_balance": "",
            "auto_credit": "",
        }
    meta = envelope_metadata(account.metadata_)
    assert meta is not None
    return {
        "name": account.name,
        "monthly_credit": cents_to_input(meta.monthly_credit),
        "cadence": meta.cadence,
        "starting_balance": "",
        "auto_credit": "on" if meta.auto_credit else "",
    }


def _envelope_editor(
    request: Request,
    account: FinanceAccount | None,
    values: dict[str, str],
    errors: list[str],
    status_code: int = 200,
) -> Response:
    return dialog(
        request,
        "partials/budget/envelope_editor.html",
        status_code,
        envelope=account,
        values=values,
        errors=errors,
        cadences=CADENCES,
        path=SECTION.path,
    )


@router.get("/envelopes/new", include_in_schema=False)
async def new_envelope_form(request: Request) -> Response:
    return _envelope_editor(request, None, _envelope_values(None), [])


@router.post("/envelopes/new", include_in_schema=False)
async def create_envelope_route(
    request: Request,
    name: Annotated[str, Form()] = "",
    monthly_credit: Annotated[str, Form()] = "",
    cadence: Annotated[str, Form()] = "monthly",
    starting_balance: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    values = {
        "name": name,
        "monthly_credit": monthly_credit,
        "cadence": cadence,
        "starting_balance": starting_balance,
        "auto_credit": "",
    }
    credit = money_to_cents(monthly_credit)
    start = money_to_cents(starting_balance)
    errors = []
    if not name.strip():
        errors.append("Give the envelope a name.")
    if credit is None or start is None or cadence not in {c["id"] for c in CADENCES}:
        errors.append("Amounts are in dollars; pick weekly or monthly.")
    if errors:
        return _envelope_editor(request, None, values, errors, 422)
    envelope = await create_envelope(
        EnvelopeCreate(
            name=name.strip(),
            monthly_credit=credit or None,
            cadence=cadence,
            starting_balance=start or 0,
        ),  # type: ignore[arg-type]
        service=service,
        owner_user_id=owner_user_id,
    )
    await service.db.commit()
    return dialog_done(f"{SECTION.path}?tab=envelopes", f"Added {envelope.name}.")


@router.delete("/envelopes/{account_id:int}", include_in_schema=False)
async def remove_envelope(
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _envelope(service, account_id, owner_user_id)
    await service.soft_delete_account(account_id, owner_user_id=owner_user_id)
    await service.db.commit()
    return close_dialog(
        with_toast(Response(status_code=200), f"Removed {account.name}.")
    )
