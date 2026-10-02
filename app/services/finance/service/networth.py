"""Net worth, the balance series, and the Overview roll-ups.

One mixin of the ``FinanceService`` facade: every method here forwards
to the matching domain module as ``module.func(self.db, ...)``.
"""

from __future__ import annotations

from datetime import date

from app.services.finance.domains.ledger import networth
from app.services.finance.models import FinanceNetWorthSnapshot
from app.services.finance.schemas import (
    FinanceHealth,
    FinanceStatusSummary,
    NetWorthByType,
    NetWorthResponse,
)
from app.services.finance.service.base import FinanceServiceBase
from app.services.finance.utils import DEFAULT_CURRENCY


class NetWorthMixin(FinanceServiceBase):
    """Net worth, the balance series, and the Overview roll-ups."""

    async def account_rollup(
        self, *, owner_user_id: int | None = None
    ) -> tuple[int, int, int]:
        return await networth.account_rollup(
            self.db,
            owner_user_id=owner_user_id,
        )

    async def connection_rollup(
        self, *, owner_user_id: int | None = None
    ) -> tuple[int, int]:
        return await networth.connection_rollup(
            self.db,
            owner_user_id=owner_user_id,
        )

    async def asset_liability_totals(
        self, *, owner_user_id: int | None = None
    ) -> tuple[int, int]:
        return await networth.asset_liability_totals(
            self.db,
            owner_user_id=owner_user_id,
        )

    async def get_net_worth(
        self, *, owner_user_id: int | None = None, currency: str = DEFAULT_CURRENCY
    ) -> NetWorthResponse:
        return await networth.get_net_worth(
            self.db,
            owner_user_id=owner_user_id,
            currency=currency,
        )

    async def get_net_worth_series(
        self,
        *,
        owner_user_id: int | None = None,
        days: int = 90,
        currency: str = DEFAULT_CURRENCY,
        account_ids: list[int] | None = None,
        without_house: bool = False,
        until: date | None = None,
    ) -> list[FinanceNetWorthSnapshot]:
        return await networth.get_net_worth_series(
            self.db,
            owner_user_id=owner_user_id,
            days=days,
            currency=currency,
            account_ids=account_ids,
            without_house=without_house,
            until=until,
        )

    async def net_worth_by_type(
        self,
        *,
        owner_user_id: int | None = None,
        days: int = 90,
        account_ids: list[int] | None = None,
        without_house: bool = False,
        until: date | None = None,
    ) -> NetWorthByType:
        return await networth.net_worth_by_type(
            self.db,
            owner_user_id=owner_user_id,
            days=days,
            account_ids=account_ids,
            without_house=without_house,
            until=until,
        )

    async def get_status_summary(
        self, *, owner_user_id: int | None = None, currency: str = DEFAULT_CURRENCY
    ) -> FinanceStatusSummary:
        return await networth.get_status_summary(
            self.db,
            owner_user_id=owner_user_id,
            currency=currency,
        )

    async def health(self, *, owner_user_id: int | None = None) -> FinanceHealth:
        return await networth.health(self.db, owner_user_id=owner_user_id)
