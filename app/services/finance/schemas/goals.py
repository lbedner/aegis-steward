"""Goal request and response shapes.

Split from ``schemas.py`` so the goal contract - the one clients, the
dashboard and the analyst all render - reads on its own screen. Imported
back into ``schemas`` so ``from app.services.finance.schemas import
GoalResponse`` keeps working; this module is the definition, that one is
the front door.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.schema import known
from app.services.finance.constants import (
    GOAL_CONTRIBUTION_KINDS,
    GOAL_MAX_TARGET_FACTOR,
    GOAL_STATUSES,
    GOAL_TARGET_RULES,
)


class GoalCreate(BaseModel):
    """POST /goals: a virtual goal by ``name``, or flag an existing real
    account as a linked goal by ``account_id`` - exactly one of the two."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    account_id: int | None = None
    # Optional under a relative rule, which computes the cents itself.
    target_amount: int | None = Field(default=None, gt=0)
    target_rule: str = "fixed"
    target_factor: int | None = Field(default=None, gt=0, le=GOAL_MAX_TARGET_FACTOR)
    # Cash accounts the run rate is measured on; empty means all of them.
    target_scope: list[int] = Field(default_factory=list)
    target_date: date | None = None
    monthly_contribution: int | None = Field(default=None, ge=0)
    contribution_kind: str = "fixed"
    contribution_pct_bps: int | None = Field(default=None, gt=0, le=10_000)
    priority: int = 100
    auto_contribute: bool = False
    _known_rule = field_validator("target_rule")(known(tuple(GOAL_TARGET_RULES)))
    _known_kind = field_validator("contribution_kind")(
        known(tuple(GOAL_CONTRIBUTION_KINDS))
    )


class GoalUpdate(BaseModel):
    """PATCH /goals/{id} - only provided fields change."""

    target_amount: int | None = Field(default=None, gt=0)
    target_rule: str | None = None
    target_factor: int | None = Field(default=None, gt=0, le=GOAL_MAX_TARGET_FACTOR)
    target_scope: list[int] | None = None
    target_date: date | None = None
    monthly_contribution: int | None = Field(default=None, ge=0)
    status: str | None = None
    auto_contribute: bool | None = None
    contribution_kind: str | None = None
    contribution_pct_bps: int | None = Field(default=None, gt=0, le=10_000)
    priority: int | None = None
    _known_rule = field_validator("target_rule")(known(tuple(GOAL_TARGET_RULES)))
    _known_status = field_validator("status")(known(GOAL_STATUSES))
    _known_kind = field_validator("contribution_kind")(
        known(tuple(GOAL_CONTRIBUTION_KINDS))
    )


class GoalContribute(BaseModel):
    amount: int = Field(gt=0)
    when: date | None = None


class GoalResponse(BaseModel):
    """A goal with its derived trio precomputed server-side - clients and
    the analyst render these, never recompute them."""

    account_id: int
    name: str
    funding: Literal["virtual", "linked"]
    status: str
    # Resolved cents as of this month's figures - under a relative rule
    # this moves without the goal being edited.
    target_amount: int
    target_rule: str
    target_factor: int | None
    target_scope: list[int]
    target_date: date | None
    monthly_contribution: int | None
    balance: int
    progress: float
    monthly_need: int
    eta: date | None  # None renders as "never"
    auto_contribute: bool
    contribution_kind: str
    contribution_pct_bps: int | None
    priority: int


class GoalListResponse(BaseModel):
    items: list[GoalResponse]
    total: int


class GoalTargetPreview(BaseModel):
    """What a relative rule would resolve to right now, computed by the
    same helper the write path uses so the dialog cannot preview one
    number and save another."""

    expenses: int  # the month's run rate on the scoped accounts
    target_amount: int
    scope: list[int]
