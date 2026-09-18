"""What is scheduled against an account inside the next window.

Read in one place because two readings is how a page and an agent come
to disagree in front of somebody: Illiana quoted a net scheduled change
for Chase checking off her own arithmetic while the account's own page
showed nothing at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger.queries.accounts import EVERYONE
from app.services.finance.domains.planning.recurring import queries
from app.services.finance.utils import current_date

# One full monthly cycle with margin, so a mid-month plan sees next
# month's rent.
WINDOW_DAYS = 35


@dataclass(frozen=True)
class Scheduled:
    """One expected movement. Signed the way the ledger signs one:
    money leaving is negative, so a list adds up without a rule."""

    name: str
    when: date
    amount_cents: int


@dataclass(frozen=True)
class Total:
    """What a list of scheduled movements comes to."""

    items: int
    inflow: int
    outflow: int
    net: int


def totalled(scheduled: list[Scheduled]) -> Total:
    """The sum, with the two directions kept apart: "nine hundred out
    and ninety in" is a different sentence from "eight hundred out"."""
    inflow = sum(one.amount_cents for one in scheduled if one.amount_cents > 0)
    outflow = -sum(one.amount_cents for one in scheduled if one.amount_cents < 0)
    return Total(
        items=len(scheduled),
        inflow=inflow,
        outflow=outflow,
        net=inflow - outflow,
    )


async def scheduled_by_account(
    db: AsyncSession,
    *,
    owner_user_id: int | None = None,
    subject_id: int | None = EVERYONE,
    days: int = WINDOW_DAYS,
) -> dict[int, list[Scheduled]]:
    """Every live stream falling inside the window, by the account it
    lands in. A stream naming no account is left out: it has nowhere to
    land, which is a thing to fix rather than a flow to count."""
    today = current_date()
    horizon = today + timedelta(days=days)
    found: dict[int, list[Scheduled]] = {}
    streams = await queries.active_streams(
        db, owner_user_id=owner_user_id, subject_id=subject_id
    )
    for stream in streams:
        when = stream.next_expected_date
        if stream.account_id is None or when is None or not today <= when <= horizon:
            continue
        sign = 1 if stream.direction == "inflow" else -1
        found.setdefault(stream.account_id, []).append(
            Scheduled(
                name=stream.name, when=when, amount_cents=sign * abs(stream.amount)
            )
        )
    return found
