"""Changing a goal: only what is given changes.

Lifted out of the API handler, where the merge lived alone, so the
Budget page and the assistant's ``goal.update`` card change a goal the
same way (#166). A second copy of this merge is how the dialog and the
card would come to disagree about what "leave the rule alone" means.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger import accounts
from app.services.finance.domains.planning import allocation
from app.services.finance.domains.planning.goals import (
    goal_metadata,
    set_auto_contribute,
    set_goal_metadata,
)
from app.services.finance.models import FinanceAccount


async def update_goal(
    db: AsyncSession,
    account_id: int,
    changes: dict[str, Any],
    *,
    owner_user_id: int | None,
) -> FinanceAccount | None:
    """Apply ``changes`` (the ``GoalUpdate`` field names; absent means
    unchanged) to a goal, or None when the account is not a goal."""
    account = await accounts.get_account(db, account_id, owner_user_id=owner_user_id)
    meta = goal_metadata(account.metadata_) if account is not None else None
    if account is None or meta is None:
        return None

    def given(name: str, standing: Any) -> Any:
        return changes[name] if changes.get(name) is not None else standing

    rule = given("target_rule", meta.target_rule)
    factor = given("target_factor", meta.target_factor)
    scope = given("target_scope", meta.target_scope)
    if rule == "fixed":
        factor = None
        scope = []
    stored_target = given("target_amount", meta.target_amount)
    if rule != "fixed":
        figures = await allocation.month_figures(
            db, owner_user_id=owner_user_id, today=datetime.now(UTC).date()
        )
        stored_target = (
            allocation.target_for_rule(
                rule=rule, factor=factor, figures=figures, scope=tuple(scope)
            )
            or stored_target
        )
    account.metadata_ = set_goal_metadata(
        account.metadata_,
        target_amount=stored_target,
        target_rule=rule,
        target_factor=factor,
        target_scope=scope,
        target_date=given("target_date", meta.target_date),
        monthly_contribution=given("monthly_contribution", meta.monthly_contribution),
        status=given("status", meta.status),
        contribution_kind=given("contribution_kind", meta.contribution_kind),
        contribution_bps=given("contribution_pct_bps", meta.contribution_bps),
        priority=given("priority", meta.priority),
    )
    if changes.get("auto_contribute") is not None:
        account.metadata_ = set_auto_contribute(
            account.metadata_, changes["auto_contribute"]
        )
    db.add(account)
    await db.flush()
    return account
