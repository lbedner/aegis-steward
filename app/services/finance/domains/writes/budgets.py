"""A budget limit as a card (#265): what a month allows for one category
or one payee.

She could read every limit (``budget``) and change none, so a plan to cut
spending ended at "I can change those limits if you want" with no card to
change them through. The card runs ``upsert_budget_line``, the function
the Budget page runs, so a limit set by a card and one set by hand are set
the same way. Like goals and envelopes, it moves no money.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.detection.insights.formatting import format_usd
from app.services.finance.domains.ledger import categories
from app.services.finance.domains.planning.budgets.lines import (
    get_or_create_budget,
    lines_in_force,
    upsert_budget_line,
)
from app.services.finance.domains.planning.budgets.outlook import parse_budget_goal
from app.services.finance.schemas import ChangeDisplayRow
from app.services.finance.utils import current_period_month

_NO_MONEY = "moves none: it is what the month allows, not a transfer"


class BudgetLimitPayload(BaseModel):
    """One limit: exactly one of a category, a payee line's key (from
    ``budget``), or a payee named in words; and what the month allows."""

    model_config = ConfigDict(extra="forbid")

    category_id: int | None = None
    payee_key: str | None = None
    payee: str | None = None
    limit_cents: int = Field(ge=0)
    # YYYYMM; None is the current month. Later months inherit it.
    month: int | None = None

    @model_validator(mode="after")
    def _one_target(self) -> BudgetLimitPayload:
        targets = [self.category_id, self.payee_key, self.payee]
        if sum(target is not None for target in targets) != 1:
            raise ValueError("Give exactly one of category_id, payee_key or payee.")
        if self.month is not None and not 1 <= self.month % 100 <= 12:
            raise ValueError("month is YYYYMM.")
        return self


async def _target(
    db: AsyncSession, payload: BudgetLimitPayload, owner_user_id: int | None
) -> tuple[int | None, str | None, str]:
    """(category_id, payee_key, what the card calls it)."""
    if payload.category_id is not None:
        names = await categories.category_names(db, {payload.category_id})
        if payload.category_id not in names:
            raise ValueError(f"There is no category {payload.category_id}.")
        return payload.category_id, None, names[payload.category_id]
    if payload.payee_key is not None:
        return None, payload.payee_key, payload.payee_key
    # A payee in words is found the way the Budget page's goal box finds
    # it: the highest-spending payee of the last 90 days that it names.
    found = await parse_budget_goal(
        db, owner_user_id=owner_user_id, text=payload.payee or ""
    )
    if found.target_type != "payee" or not found.payee_key:
        raise ValueError(f"No payee matching {payload.payee!r} in the last 90 days.")
    return None, found.payee_key, found.payee_label or payload.payee or ""


async def _in_force(
    db: AsyncSession,
    owner_user_id: int | None,
    month: int,
    category_id: int | None,
    payee_key: str | None,
) -> int | None:
    """What the month allows the target now: its own line or the one it
    inherits, else None."""
    budget = await get_or_create_budget(
        db, owner_user_id=owner_user_id, period_month=month
    )
    assert budget.id is not None
    for line in await lines_in_force(db, budget_id=budget.id, period_month=month):
        if (line.category_id, line.payee_key) == (category_id, payee_key):
            return line.allocated_amount
    return None


def _month_name(month: int) -> str:
    return date(month // 100, month % 100, 1).strftime("%B %Y")


async def budget_limit_describe(
    db: AsyncSession, payload: BudgetLimitPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    category_id, payee_key, name = await _target(db, payload, owner_user_id)
    month = payload.month or current_period_month()
    was = await _in_force(db, owner_user_id, month, category_id, payee_key)
    if was == payload.limit_cents:
        raise ValueError(f"This would change nothing about the {name} limit.")
    rows = [
        ChangeDisplayRow(label="Limit", value=name),
        ChangeDisplayRow(
            label="Per month",
            value=f"{format_usd(was) if was is not None else 'none'} → "
            f"{format_usd(payload.limit_cents)}",
        ),
    ]
    if payload.month is not None and payload.month != current_period_month():
        rows.append(ChangeDisplayRow(label="From", value=_month_name(month)))
    rows.append(ChangeDisplayRow(label="Money", value=_NO_MONEY))
    return rows


async def budget_limit_execute(
    db: AsyncSession, payload: BudgetLimitPayload, owner_user_id: int | None
) -> dict[str, Any]:
    category_id, payee_key, name = await _target(db, payload, owner_user_id)
    line = await upsert_budget_line(
        db,
        owner_user_id=owner_user_id,
        period_month=payload.month,
        category_id=category_id,
        payee_key=payee_key,
        payee_label=name if payee_key else None,
        allocated_amount=payload.limit_cents,
    )
    return {"line_id": line.id}
