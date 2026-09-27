"""The household's budget draws only from the household's money (#276).

On Projected, narrowing to Dad's HVCU account still charged the household
budget against it: about $5,417 over 90 days on his view, and Chase plus
HVCU did not equal the two viewed apart. Budget lines and goals are the
household's plans, so they draw only from a projection that holds
household cash (a cash account nobody else's).
"""

from datetime import date

import pytest

from app.services.finance.service import FinanceService

TODAY = date(2026, 8, 2)


async def _world(svc: FinanceService) -> tuple[int, int, int]:
    """Household checking, Dad's HVCU, and a household groceries budget."""
    dad = await svc.create_subject(name="Dad", owner_user_id=1)
    ours = await svc.create_manual_account(
        name="Chase", account_type="checking", classification="asset", owner_user_id=1
    )
    theirs = await svc.create_manual_account(
        name="HVCU", account_type="checking", classification="asset", owner_user_id=1
    )
    await svc.assign_subject(theirs.id, dad.id, owner_user_id=1)
    groceries = await svc.get_or_create_category_from_hint("Food:Groceries")
    await svc.upsert_budget_line(
        owner_user_id=1,
        period_month=202608,
        category_id=groceries.id,
        payee_key=None,
        payee_label=None,
        allocated_amount=40_000,
    )
    return ours.id, theirs.id, dad.id


def _budget(result) -> list:
    return [p for p in result.points if p.category == "Food:Groceries"]


class TestWhoseBudget:
    @pytest.mark.asyncio
    async def test_the_household_view_still_draws_its_budget(
        self, svc: FinanceService
    ) -> None:
        await _world(svc)
        result = await svc.project_balances(owner_user_id=1, days=60, today=TODAY)
        assert _budget(result)

    @pytest.mark.asyncio
    async def test_someone_elses_account_alone_draws_none(
        self, svc: FinanceService
    ) -> None:
        _ours, theirs, _dad = await _world(svc)
        result = await svc.project_balances(
            owner_user_id=1, days=60, today=TODAY, account_ids=[theirs]
        )
        assert not _budget(result), "the household budget bled into Dad's account"

    @pytest.mark.asyncio
    async def test_their_money_as_a_person_draws_none(self, svc: FinanceService) -> None:
        _ours, _theirs, dad = await _world(svc)
        result = await svc.project_balances(
            owner_user_id=1, days=60, today=TODAY, subject_id=dad
        )
        assert not _budget(result)

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=4)  # three projections compared, on purpose
    async def test_together_is_the_sum_of_apart(self, svc: FinanceService) -> None:
        ours, theirs, _dad = await _world(svc)
        both = await svc.project_balances(
            owner_user_id=1, days=60, today=TODAY, account_ids=[ours, theirs]
        )
        mine = await svc.project_balances(
            owner_user_id=1, days=60, today=TODAY, account_ids=[ours]
        )
        his = await svc.project_balances(
            owner_user_id=1, days=60, today=TODAY, account_ids=[theirs]
        )
        assert _budget(both), "the household's cash is in view, so its budget draws"
        assert both.upcoming_total == mine.upcoming_total + his.upcoming_total
