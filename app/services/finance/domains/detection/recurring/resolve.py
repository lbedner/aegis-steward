"""Which stream a detected payee group lands on.

One payee on one account used to mean exactly one bill, because the key
WAS the payee. A payee that really sells two things (Anthropic billing a
subscription and API usage) needs sibling keys off the same base, and a
CONFIRMED bill owns its membership outright - regrouping must never
absorb members into a bill the user settled.
"""

from collections.abc import Mapping, Sequence
from datetime import date

from sqlmodel import or_
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.constants import CADENCES
from app.services.finance.domains.detection import queries
from app.services.finance.domains.detection.recurring.cadence import (
    AMOUNT_TOLERANCE,
    BILL_RESEMBLANCE_TOLERANCE,
    SPLIT_MARK,
)
from app.services.finance.models import FinanceRecurringStream

# Separator for the second, third, ... bill a single payee runs on one
# account. ``normalized_payee`` is purely the detected-stream unique key
# and is never displayed (Bills & Income shows ``name``), so widening it
# this way needs no migration - the unique index still does its job, it
# just stops forcing one payee to mean one bill.


async def _sibling_streams(
    db: AsyncSession, *, owner_user_id: int, account_id: int, direction: str, base: str
) -> list[FinanceRecurringStream]:
    """Every live stream on this payee+account+direction, base key or a
    split of it."""
    rows = await queries.sibling_streams_raw(
        db,
        owner_user_id=owner_user_id,
        account_id=account_id,
        direction=direction,
    )
    return [
        s
        for s in rows
        if s.normalized_payee == base
        or (s.normalized_payee or "").startswith(base + SPLIT_MARK)
    ]


async def _resolve_payee_key(
    db: AsyncSession,
    *,
    owner_user_id: int,
    account_id: int,
    direction: str,
    base: str,
    member_ids: set[int],
) -> tuple[str, FinanceRecurringStream | None]:
    """The key this group of members should land on, and the stream already
    holding it (if any).

    One payee on one account used to mean exactly one bill, because the key
    WAS the payee. That is wrong for a payee that really does sell you two
    things - Anthropic billing a Claude subscription and API usage, Amazon
    billing Prime and AWS - and it is what would quietly undo an exclusion:
    drop a charge from a bill, and the next pass regroups it under the same
    payee key, finds the bill, and puts it straight back.

    So a CONFIRMED bill owns its membership. If one exists on the base key
    and these members are not its members, they are something else, and
    they get their own key (``merchant:12#2``) rather than being merged in.
    """
    siblings = await _sibling_streams(
        db,
        owner_user_id=owner_user_id,
        account_id=account_id,
        direction=direction,
        base=base,
    )
    # A stream these members already sit in is the one they belong to -
    # this is the ordinary re-run, and it must absorb rather than fork.
    for stream in siblings:
        if stream.id is not None and member_ids:
            if await queries.any_member_linked(
                db, stream_id=stream.id, member_ids=member_ids
            ):
                return str(stream.normalized_payee or base), stream
    on_base = next((s for s in siblings if s.normalized_payee == base), None)
    if on_base is not None and on_base.is_user_confirmed:
        taken = {s.normalized_payee for s in siblings}
        index = 2
        while f"{base}{SPLIT_MARK}{index}" in taken:
            index += 1
        return f"{base}{SPLIT_MARK}{index}", None
    return base, on_base


def _keys_nest(a: str, b: str) -> bool:
    """One key's tokens contained in the other's = the same identity
    under two spellings ("HBO MAX" and "HBO MAX NEW YORK NY"). Overlap
    without nesting (ANTHROPIC SUBS / ANTHROPIC USAGE) is two bills."""
    ta = set(a.split(SPLIT_MARK, 1)[0].split())
    tb = set(b.split(SPLIT_MARK, 1)[0].split())
    return bool(ta) and bool(tb) and (ta <= tb or tb <= ta)


def _alongside(
    dates: Sequence[date], bill_dates: Sequence[date], frequency: str
) -> bool:
    """Charged beside the bill, not instead of it: twice or more within a
    third of a cycle of one of its own payments."""
    cadence = CADENCES.get(frequency)
    near = (cadence.detect_days if cadence else 30) / 3
    beside = sum(1 for d in dates if any(abs((d - b).days) <= near for b in bill_dates))
    return beside >= 2


def _is_the_bill_again(
    bills: list[FinanceRecurringStream],
    keys: Sequence[str],
    frequency: str,
    median_amount: int,
    *,
    payee_id: int | None = None,
    bill_payees: Mapping[int, set[int]] | None = None,
    bill_dates: Mapping[int, Sequence[date]] | None = None,
    dates: Sequence[date] = (),
) -> bool:
    """ "Is it HBO or not": a group that is a confirmed bill's own payee,
    on the same cadence, at roughly the bill's price, is that bill's own
    history - a dead price era, a descriptor that grew a city tail, a
    payee named after the bill was settled - and reproposing it beside
    the settled bill is the one duplicate detection must never make.

    Same payee: any of ``keys`` NESTS with the bill's key, or the group's
    ``payee_id`` is the bill's, or one its payments wear
    (``bill_payees``). Nesting, not overlap: ANTHROPIC SUBS and
    ANTHROPIC USAGE share a token but nest neither way, and stay two
    bills.

    Not when it is charged ALONGSIDE the bill at another price: two
    memberships at one car wash, $32.38 and $43.20 a day apart each
    month, are two bills, while a bill's old price ends where its new one
    begins (#337)."""
    for bill in bills:
        if bill.frequency != frequency:
            continue
        average = float(bill.average_amount or 0)
        # Beside it at another price is a second bill; at its own price it
        # is the bill's payment arriving twice (a file row a sync missed).
        if abs(median_amount - average) > AMOUNT_TOLERANCE * average and _alongside(
            dates, (bill_dates or {}).get(bill.id or 0, ()), frequency
        ):
            continue
        wears = (bill_payees or {}).get(bill.id or 0, set())
        same_payee = payee_id is not None and (
            payee_id == bill.merchant_id or payee_id in wears
        )
        if not same_payee and not any(
            _keys_nest(key, bill.normalized_payee or "") for key in keys
        ):
            continue
        if average and abs(median_amount - average) <= (
            BILL_RESEMBLANCE_TOLERANCE * average
        ):
            return True
    return False


async def _dismissed_twin(
    db: AsyncSession,
    *,
    owner_user_id: int,
    account_id: int | None,
    direction: str,
    payee: str,
    frequency: str,
    average_amount: int,
) -> FinanceRecurringStream | None:
    """The dismissed proposal this key is a respelling of, if any.

    Same identity bar as the confirmed-bill guard: nested key tokens,
    same cadence, price within ``BILL_RESEMBLANCE_TOLERANCE``. Only
    dismissals qualify - live proposals are found by their exact key."""
    rows = await queries.stream_rows_where(
        db,
        [
            FinanceRecurringStream.owner_user_id == owner_user_id,
            FinanceRecurringStream.account_id == account_id,
            FinanceRecurringStream.direction == direction,
            FinanceRecurringStream.source == "derived",
            FinanceRecurringStream.is_user_confirmed.is_(False),
            FinanceRecurringStream.provider_stream_id.is_(None),
            or_(
                FinanceRecurringStream.is_muted.is_(True),
                FinanceRecurringStream.deleted_at.is_not(None),
            ),
        ],
    )
    for row in rows:
        if row.frequency != frequency:
            continue
        if not _keys_nest(payee, row.normalized_payee or ""):
            continue
        average = float(row.average_amount or 0)
        if average and abs(average_amount - average) <= (
            BILL_RESEMBLANCE_TOLERANCE * average
        ):
            return row
    return None
