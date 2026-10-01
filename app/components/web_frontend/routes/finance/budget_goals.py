"""Goals: the editor, the contribution rules, and the target preview.

Split out of ``budget.py`` at the 500-line budget. A goal is its own
small domain - a target, a cadence, and the arithmetic that says when it
lands - and it was the largest single section of that file.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from pydantic import BaseModel
from starlette.responses import Response

from app.components.backend.api.finance.goals import (
    create_goal,
    goal_response,
    preview_goal_target,
    update_goal,
)
from app.components.web_frontend.filters import (
    as_options,
    cents_to_input,
    money,
    money_to_cents,
)
from app.components.web_frontend.rendering import (
    close_dialog,
    dialog_done,
    with_toast,
)
from app.components.web_frontend.routes.finance.budget_display import (
    SECTION,
    budget_dialog,
    eta_caption,
    move_dialog,
    owned_account,
    remove_dialog,
    removed,
    with_strip,
)
from app.services.finance.constants import GOAL_CONTRIBUTION_KINDS, GOAL_TARGET_RULES
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.domains.planning.goals import goal_metadata
from app.services.finance.models import FinanceAccount
from app.services.finance.schemas import (
    GoalCreate,
    GoalResponse,
    GoalUpdate,
)
from app.services.finance.service import FinanceService
from app.services.finance.utils import positive_cents

router = APIRouter(prefix=SECTION.path)


# --- goals -----------------------------------------------------------------------


async def _goal(
    service: FinanceService, account_id: int, owner_user_id: int | None
) -> FinanceAccount:
    return await owned_account(service, account_id, owner_user_id, goal_metadata)


async def _goal_card(
    request: Request,
    service: FinanceService,
    owner_user_id: int | None,
    account: FinanceAccount,
    oob: bool = False,
) -> Response:
    await service.db.commit()
    goal = await goal_response(service, account)
    return await with_strip(
        request,
        service,
        owner_user_id,
        "partials/budget/goal_card.html",
        {"goal": goal, "eta": eta_caption(goal), "oob": oob},
    )


def _contribute_dialog(
    request: Request, account: FinanceAccount, errors: list[str], status_code: int = 200
) -> Response:
    return move_dialog(
        request,
        title=f"Add to {account.name}",
        action=f"{SECTION.path}/goals/{account.id}/contribute",
        label="Add",
        note=False,
        errors=errors,
        status_code=status_code,
    )


class GoalForm(BaseModel):
    """What the goal dialog posts, new or edited.

    The nine fields were listed five times: the defaults, the rebuild for
    a re-render, both routes' parameters and both calls passing them on.
    """

    name: str = ""
    target_rule: str = "fixed"
    target_amount: str = ""
    target_factor: str = ""
    target_date: str = ""
    contribution_kind: str = "fixed"
    monthly_contribution: str = ""
    contribution_pct: str = ""
    auto_contribute: str = ""
    # Cash accounts the run rate is measured on; none means all of them.
    scope: list[int] = []
    # New goals only: flag this account as the goal instead of a virtual one.
    linked_account_id: str = ""


def _goal_form(goal: GoalResponse) -> GoalForm:
    """The dialog's fields for a goal that exists."""
    return GoalForm(
        name=goal.name,
        target_rule=goal.target_rule,
        target_amount=cents_to_input(goal.target_amount),
        target_factor=str(goal.target_factor or ""),
        target_date=goal.target_date.isoformat() if goal.target_date else "",
        contribution_kind=goal.contribution_kind,
        monthly_contribution=cents_to_input(goal.monthly_contribution),
        contribution_pct=(
            f"{goal.contribution_pct_bps / 100:g}" if goal.contribution_pct_bps else ""
        ),
        auto_contribute="on" if goal.auto_contribute else "",
        scope=list(goal.target_scope),
    )


async def _goal_editor(
    request: Request,
    service: FinanceService,
    owner_user_id: int | None,
    goal: GoalResponse | None,
    values: GoalForm,
    errors: list[str],
    status_code: int = 200,
) -> Response:
    accounts, _total = await service.list_accounts(
        owner_user_id=owner_user_id, page_size=500
    )
    return budget_dialog(
        request,
        "partials/budget/goal_editor.html",
        status_code,
        goal=goal,
        values=values,
        errors=errors,
        rules=as_options(GOAL_TARGET_RULES),
        kinds=as_options(GOAL_CONTRIBUTION_KINDS),
        accounts=accounts,
    )


def _parse_goal(form: GoalForm) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    parsed: dict[str, Any] = {"name": form.name.strip() or None}
    rule = form.target_rule
    if rule not in GOAL_TARGET_RULES:
        errors.append("Pick how the target is sized.")
    parsed["target_rule"] = rule
    if rule == "fixed":
        cents = positive_cents(form.target_amount)
        if cents is None:
            errors.append("Enter the target in dollars.")
        parsed["target_amount"] = cents
        parsed["target_factor"] = None
        parsed["target_scope"] = []
    else:
        try:
            parsed["target_factor"] = int(form.target_factor)
        except ValueError:
            errors.append("Enter how many months of expenses.")
        parsed["target_amount"] = None
        parsed["target_scope"] = form.scope
    parsed["target_date"] = (
        date.fromisoformat(form.target_date) if form.target_date else None
    )
    kind = form.contribution_kind
    if kind not in GOAL_CONTRIBUTION_KINDS:
        errors.append("Pick how to contribute.")
    parsed["contribution_kind"] = kind
    parsed["monthly_contribution"] = None
    parsed["contribution_pct_bps"] = None
    if kind == "fixed" and form.monthly_contribution.strip():
        monthly = money_to_cents(form.monthly_contribution)
        if monthly is None:
            errors.append("Enter the monthly amount in dollars.")
        parsed["monthly_contribution"] = monthly
    if kind == "percent_income":
        try:
            parsed["contribution_pct_bps"] = int(float(form.contribution_pct) * 100)
        except ValueError:
            errors.append("Enter the percent of income.")
    parsed["auto_contribute"] = form.auto_contribute == "on"
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
            f"= {money(preview.target_amount)} "
            f"({factor} months × {money(preview.expenses)} of monthly expenses)"
        )
    return budget_dialog(request, "partials/budget/target_preview.html", text=text)


@router.get("/goals/new", include_in_schema=False)
async def new_goal_form(
    request: Request,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    return await _goal_editor(request, service, owner_user_id, None, GoalForm(), [])


@router.post("/goals/new", include_in_schema=False)
async def create_goal_route(
    request: Request,
    form: Annotated[GoalForm, Form()],
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    parsed, errors = _parse_goal(form)
    if not form.linked_account_id and not parsed["name"]:
        errors.insert(0, "Give the goal a name.")
    if errors:
        return await _goal_editor(
            request, service, owner_user_id, None, form, errors, 422
        )
    if form.linked_account_id:
        parsed["name"] = None
        parsed["account_id"] = int(form.linked_account_id)
    try:
        goal = await create_goal(
            GoalCreate(**parsed), service=service, owner_user_id=owner_user_id
        )
    except HTTPException as exc:
        return await _goal_editor(
            request, service, owner_user_id, None, form, [str(exc.detail)], 422
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
        request, service, owner_user_id, goal, _goal_form(goal), []
    )


@router.post("/goals/{account_id:int}/edit", include_in_schema=False)
async def edit_goal(
    request: Request,
    account_id: int,
    form: Annotated[GoalForm, Form()],
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _goal(service, account_id, owner_user_id)
    goal = await goal_response(service, account)
    parsed, errors = _parse_goal(form)
    if errors:
        return await _goal_editor(
            request, service, owner_user_id, goal, form, errors, 422
        )
    parsed.pop("name")
    await update_goal(
        account_id, GoalUpdate(**parsed), service=service, owner_user_id=owner_user_id
    )
    if form.name.strip() and form.name.strip() != account.name:
        account.name = form.name.strip()
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
    return _contribute_dialog(request, account, [])


@router.post("/goals/{account_id:int}/contribute", include_in_schema=False)
async def contribute(
    request: Request,
    account_id: int,
    amount: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _goal(service, account_id, owner_user_id)
    cents = positive_cents(amount)
    if cents is None:
        return _contribute_dialog(
            request, account, ["Enter an amount in dollars."], 422
        )
    try:
        await service.contribute_to_goal(
            account_id, amount=cents, owner_user_id=owner_user_id
        )
    except ValueError as exc:  # a linked goal: its contributions are real transfers
        return _contribute_dialog(request, account, [str(exc)], 422)
    response = await _goal_card(request, service, owner_user_id, account, oob=True)
    return close_dialog(
        with_toast(response, f"Added {money(cents)} to {account.name}.")
    )


@router.get("/goals/{account_id:int}/remove", include_in_schema=False)
async def remove_goal_form(
    request: Request,
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _goal(service, account_id, owner_user_id)
    return remove_dialog(
        request,
        title=f"Remove {account.name}?",
        body="A virtual goal is deleted; a linked account only stops being a goal.",
        url=f"{SECTION.path}/goals/{account_id}",
    )


@router.delete("/goals/{account_id:int}", include_in_schema=False)
async def remove_goal(
    request: Request,
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    from app.components.backend.api.finance.goals import delete_goal

    account = await _goal(service, account_id, owner_user_id)
    await delete_goal(account_id, service=service, owner_user_id=owner_user_id)
    return await removed(
        request,
        service,
        owner_user_id,
        f"goal-{account_id}",
        f"Removed {account.name}.",
    )
