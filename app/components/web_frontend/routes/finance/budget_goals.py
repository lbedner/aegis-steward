"""Goals: the editor, the contribution rules, and the target preview.

Split out of ``budget.py`` at the 500-line budget. A goal is its own
small domain - a target, a cadence, and the arithmetic that says when it
lands - and it was the largest single section of that file.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from starlette.responses import Response

from app.components.backend.api.finance.goals import (
    create_goal,
    goal_response,
    preview_goal_target,
    update_goal,
)
from app.components.web_frontend.filters import cents_to_input, money, money_to_cents
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import (
    close_dialog,
    dialog,
    dialog_done,
    with_toast,
)
from app.components.web_frontend.routes.finance.budget_display import (
    CONTRIBUTION_KINDS,
    TARGET_RULES,
    eta_caption,
)
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.domains.planning.goals import goal_metadata
from app.services.finance.models import FinanceAccount
from app.services.finance.schemas import (
    GoalCreate,
    GoalResponse,
    GoalUpdate,
)
from app.services.finance.service import FinanceService

SECTION = section("budget")
router = APIRouter(prefix=SECTION.path)


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
            f"= {money(preview.target_amount)} "
            f"({factor} months × {money(preview.expenses)} of monthly expenses)"
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
