"""What the budget's three route modules share: the section, its
dialog, the strip a write re-sends, how a removal answers, the choices
its forms offer, and the words a goal's progress is said in. The strip
itself, its popups and the pager read the way ``budgets.strip`` words
them, for both frontends.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request
from starlette.responses import Response

from app.components.backend.api.finance.budgets import budget_summary
from app.components.web_frontend.filters import money
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import (
    close_dialog,
    dialog,
    templates,
    with_toast,
)
from app.services.finance.domains.planning.budgets import strip
from app.services.finance.models import FinanceAccount
from app.services.finance.schemas import BudgetSummaryResponse, GoalResponse
from app.services.finance.service import FinanceService

SECTION = section("budget")


async def stats_context(
    service: FinanceService,
    owner_user_id: int | None,
    account_ids: list[int] | None,
    month: int | None = None,
) -> tuple[BudgetSummaryResponse, dict[str, Any]]:
    """A month's summary (default: this one), and the strip drawn from it."""
    summary = await budget_summary(
        month=month,
        account_ids=account_ids,
        service=service,
        owner_user_id=owner_user_id,
    )
    return summary, {"cells": strip.stats_cells(summary.stats), "clickable": True}


async def with_strip(
    request: Request,
    service: FinanceService,
    owner_user_id: int | None,
    template: str,
    context: dict[str, Any],
    status_code: int = 200,
) -> Response:
    """A write's answer: ``template`` (which may render nothing) and the
    strip out of band after it. Every budget write answers this way, so
    the verdict never goes stale; the strip is attached here and only
    here."""
    await service.db.commit()
    _summary, stats = await stats_context(service, owner_user_id, None)
    return templates.TemplateResponse(
        request=request,
        name="partials/budget/with_strip.html",
        context={
            **context,
            "primary": template,
            "path": SECTION.path,
            "stats": stats,
            "strip_oob": True,
        },
        status_code=status_code,
    )


def budget_dialog(
    request: Request, template: str, /, status_code: int = 200, **context: Any
) -> Response:
    """A budget dialog: its partials build their URLs on the section path."""
    return dialog(request, template, status_code, path=SECTION.path, **context)


def move_dialog(
    request: Request,
    *,
    title: str,
    action: str,
    label: str,
    note: bool,
    errors: list[str],
    status_code: int = 200,
) -> Response:
    """The one-amount dialog: credit or spend an envelope, add to a goal."""
    return budget_dialog(
        request,
        "partials/budget/move.html",
        status_code,
        title=title,
        action=action,
        label=label,
        note=note,
        errors=errors,
    )


def remove_dialog(request: Request, *, title: str, body: str, url: str) -> Response:
    """The question every budget removal asks first."""
    return budget_dialog(
        request, "partials/budget/remove.html", title=title, body=body, url=url
    )


async def removed(
    request: Request,
    service: FinanceService,
    owner_user_id: int | None,
    element_id: str,
    toast: str,
) -> Response:
    """The answer to a confirmed removal: the element leaves the page, the
    strip moves, the dialog closes, and the toast says what went. The
    confirm swaps nothing in, so the element has to go out of band."""
    response = await with_strip(
        request,
        service,
        owner_user_id,
        "partials/removed.html",
        {"removed": element_id},
    )
    return close_dialog(with_toast(response, toast))


async def owned_account(
    service: FinanceService,
    account_id: int,
    owner_user_id: int | None,
    reader: Callable[[dict[str, Any] | None], object | None],
) -> FinanceAccount:
    """The owner's account that ``reader`` recognises (a goal, an
    envelope), or the one 404."""
    account = await service.get_account(account_id, owner_user_id=owner_user_id)
    if account is None or reader(account.metadata_) is None:
        raise HTTPException(status_code=404)
    return account


def eta_caption(goal: GoalResponse) -> str:
    if goal.status == "reached" or goal.progress >= 1:
        return "Reached"
    if goal.status == "paused":
        return "Paused"
    monthly = money(goal.monthly_need)
    if goal.contribution_kind == "percent_income":
        monthly += f" ({(goal.contribution_pct_bps or 0) / 100:g}% of income)"
    elif goal.contribution_kind == "surplus":
        monthly += " (surplus)"
    if goal.eta is None:
        return f"{monthly}/mo · at this rate: never"
    return f"{monthly}/mo · lands {goal.eta:%b %d, %Y}"
