"""The normalized shape every investment-ledger profile parses into, and the
replay check that verifies a parsed ledger before it's loaded.

House sign rule (matches ``importers/base.py``): negative means an outflow.
Here that's per-security: a row that adds shares/value to a position is
positive, one that removes shares/value is negative.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from app.services.finance.schemas import InvestmentImportPreviewResponse


class InvestmentActivity(BaseModel):
    """One normalized trade-ledger row, source-agnostic."""

    model_config = ConfigDict(frozen=True)

    trade_date: date
    security_name: str
    trade_type: (
        str  # FinanceTrade.type vocabulary: buy, sell, reinvest, fee, transfer_in, ...
    )
    units: Decimal  # signed: + adds to the position, - removes from it
    price: Decimal
    amount_cents: int  # signed cents, same direction as units
    raw_type: str  # the source's own label, for traceability
    subtype: str | None = None
    ticker: str | None = None


def replay_positions(activities: list[InvestmentActivity]) -> dict[str, Decimal]:
    """Sum signed ``units`` per security name.

    The verify step: replay a parsed ledger and compare the result to the
    source's own stated current holdings before writing anything.
    """
    positions: dict[str, Decimal] = defaultdict(Decimal)
    for activity in activities:
        positions[activity.security_name] += activity.units
    return dict(positions)


def preview_ledger(
    activities: list[InvestmentActivity],
) -> InvestmentImportPreviewResponse:
    """What a parsed ledger carries - row count, date range, and the
    replayed ending positions - without touching the database.

    Each position is valued at its security's last ledger price: the same
    mark the loader will store, and the freshest one the file itself can
    honestly claim (not a live quote).
    """
    from app.services.finance.schemas import (
        InvestmentImportPosition,
        InvestmentImportPreviewResponse,
    )

    last_price: dict[str, float] = {}
    last_date_seen: dict[str, date] = {}
    for activity in activities:
        prior = last_date_seen.get(activity.security_name)
        if prior is None or activity.trade_date >= prior:
            last_date_seen[activity.security_name] = activity.trade_date
            last_price[activity.security_name] = float(activity.price)
    positions = [
        InvestmentImportPosition(
            name=name,
            shares=float(shares),
            value=round(float(shares) * last_price[name] * 100),
        )
        for name, shares in sorted(replay_positions(activities).items())
    ]
    return InvestmentImportPreviewResponse(
        activities_parsed=len(activities),
        first_date=min(a.trade_date for a in activities),
        last_date=max(a.trade_date for a in activities),
        total_value=sum(p.value for p in positions),
        positions=positions,
    )
