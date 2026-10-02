"""Budget limits as cards: what a month allows for one category or one
payee (#265), and taking a limit off (#287).

She could read every limit (``budget``) and change none, so a plan to cut
spending ended at "I can change those limits if you want" with no card to
change them through. The card runs ``upsert_budget_line``, the function
the Budget page runs, so a limit set by a card and one set by hand are set
the same way. Asked to "just remove that Starbucks budget item", she
could only set it to $0, and the line stayed on the page: removing runs
``remove_budget_line``, the removal the page's own delete makes. Like
goals and envelopes, neither moves money.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.detection.insights.formatting import format_usd
from app.services.finance.domains.ledger import categories
from app.services.finance.domains.planning.budgets.lines import (
    line_in_force,
    remove_budget_line,
    upsert_budget_line,
)
from app.services.finance.domains.planning.budgets.outlook import parse_budget_goal
from app.services.finance.models import FinanceBudgetCategory
from app.services.finance.schemas import ChangeDisplayRow, LimitCents, PeriodMonth
from app.services.finance.utils import current_period_month, period_label

_NO_MONEY = "moves none: it is what the month allows, not a transfer"


class _BudgetLine(BaseModel):
    """Which limit: exactly one of a category, a payee line's key (from
    ``budget``), or a payee named in words; and from which month."""

    model_config = ConfigDict(extra="forbid")

    category_id: int | None = None
    payee_key: str | None = None
    payee: str | None = None
    # None is the current month. Later months inherit it.
    month: PeriodMonth | None = None

    @model_validator(mode="after")
    def _one_target(self) -> _BudgetLine:
        targets = [self.category_id, self.payee_key, self.payee]
        if sum(target is not None for target in targets) != 1:
            raise ValueError("Give exactly one of category_id, payee_key or payee.")
        return self


class BudgetLimitPayload(_BudgetLine):
    """One limit and what the month allows."""

    limit_cents: LimitCents
    # True: what the month leaves, or overspends, carries into the next and
    # keeps stacking (#360). False stops it; leave it out to keep it as is.
    rollover: bool | None = None


class BudgetRemovePayload(_BudgetLine):
    """One limit to take off the budget."""


async def _target(
    db: AsyncSession, payload: _BudgetLine, owner_user_id: int | None
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
    # it: the highest-spending payee of its baseline window that it names.
    found = await parse_budget_goal(
        db, owner_user_id=owner_user_id, text=payload.payee or ""
    )
    if found.target_type != "payee" or not found.payee_key:
        raise ValueError(
            f"No payee matching {payload.payee!r} in the last {found.baseline_days} days."
        )
    return None, found.payee_key, found.payee_label or payload.payee or ""


def _per_month(cents: int | None) -> str:
    return format_usd(cents) if cents is not None else "none"


def _card(
    payload: _BudgetLine, name: str, was: int | None, becomes: int | None
) -> list[ChangeDisplayRow]:
    """What a limit card says: which limit, what the month allows before
    and after, from when, and that no money moves."""
    rows = [
        ChangeDisplayRow(label="Limit", value=name),
        ChangeDisplayRow(
            label="Per month", value=f"{_per_month(was)} → {_per_month(becomes)}"
        ),
    ]
    if payload.month is not None and payload.month != current_period_month():
        rows.append(ChangeDisplayRow(label="From", value=period_label(payload.month)))
    rows.append(ChangeDisplayRow(label="Money", value=_NO_MONEY))
    return rows


async def _now(
    db: AsyncSession, payload: _BudgetLine, owner_user_id: int | None
) -> tuple[int | None, str | None, str, FinanceBudgetCategory | None]:
    """The target, its name, and the limit its month runs on now."""
    category_id, payee_key, name = await _target(db, payload, owner_user_id)
    line = await line_in_force(
        db,
        owner_user_id=owner_user_id,
        period_month=payload.month,
        category_id=category_id,
        payee_key=payee_key,
    )
    return category_id, payee_key, name, line


def _yes(rolls: bool) -> str:
    return "yes" if rolls else "no"


async def budget_limit_describe(
    db: AsyncSession, payload: BudgetLimitPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    _category_id, _payee_key, name, line = await _now(db, payload, owner_user_id)
    was = None if line is None else line.allocated_amount
    rolls = line is not None and line.rollover_enabled
    if was == payload.limit_cents and payload.rollover in (None, rolls):
        raise ValueError(f"This would change nothing about the {name} limit.")
    rows = _card(payload, name, was, payload.limit_cents)
    if payload.rollover is not None:
        rows.insert(
            2,
            ChangeDisplayRow(
                label="Rolls over", value=f"{_yes(rolls)} → {_yes(payload.rollover)}"
            ),
        )
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
        rollover_enabled=payload.rollover,
    )
    return {"line_id": line.id}


def _nothing_to_remove(name: str) -> ValueError:
    return ValueError(f"There is no {name} limit to remove.")


async def budget_remove_describe(
    db: AsyncSession, payload: BudgetRemovePayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    _category_id, _payee_key, name, line = await _now(db, payload, owner_user_id)
    if line is None:
        raise _nothing_to_remove(name)
    return _card(payload, name, line.allocated_amount, None)


async def budget_remove_execute(
    db: AsyncSession, payload: BudgetRemovePayload, owner_user_id: int | None
) -> dict[str, Any]:
    category_id, payee_key, name = await _target(db, payload, owner_user_id)
    removed = await remove_budget_line(
        db,
        owner_user_id=owner_user_id,
        period_month=payload.month,
        category_id=category_id,
        payee_key=payee_key,
    )
    if removed is None:
        raise _nothing_to_remove(name)
    return {"line_id": removed}
