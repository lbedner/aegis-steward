"""The rule-generated insight rows.

One mixin of the ``FinanceService`` facade: every method here forwards
to the matching domain module as ``module.func(self.db, ...)``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from app.services.finance.domains.detection import insights as detection
from app.services.finance.domains.planning import insights
from app.services.finance.models import FinanceInsight
from app.services.finance.schemas import CategoryMove
from app.services.finance.service.base import FinanceServiceBase
from app.services.finance.utils import current_date


class InsightsMixin(FinanceServiceBase):
    """The rule-generated insight rows."""

    async def category_moves(
        self,
        *,
        owner_user_id: int | None = None,
        today: date | None = None,
        limit: int | None = None,
    ) -> list[CategoryMove]:
        return await detection.category_moves(
            self.db,
            owner_user_id=owner_user_id,
            today=today or current_date(),
            limit=limit,
        )

    async def list_insights(
        self,
        *,
        owner_user_id: int | None = None,
        status: str | None = "new",
        insight_type: str | None = None,
        exclude_types: Sequence[str] = (),
    ) -> list[FinanceInsight]:
        return await insights.list_insights(
            self.db,
            owner_user_id=owner_user_id,
            status=status,
            insight_type=insight_type,
            exclude_types=exclude_types,
        )

    async def count_new_insights(self, *, owner_user_id: int | None = None) -> int:
        return await insights.count_new_insights(self.db, owner_user_id=owner_user_id)

    async def dismiss_insight(
        self, insight_id: int, *, owner_user_id: int | None = None
    ) -> FinanceInsight | None:
        return await insights.dismiss_insight(
            self.db,
            insight_id,
            owner_user_id=owner_user_id,
        )
