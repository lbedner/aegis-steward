"""Reading net worth back: the series, the totals, the rollups.

These are what the dashboard and the health check ask for; none
of them writes.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.constants import ACCOUNT_GROUPS, account_group
from app.services.finance.domains.ledger import queries
from app.services.finance.domains.ledger.accounts import effective_balance
from app.services.finance.domains.ledger.net_worth_rule import (
    counts_in_net_worth,
    sum_into,
)
from app.services.finance.domains.ledger.networth.snapshots import _today
from app.services.finance.domains.planning import insights
from app.services.finance.models import (
    FinanceNetWorthSnapshot,
)
from app.services.finance.schemas import (
    FinanceHealth,
    FinanceStatusSummary,
    NetWorthByType,
    NetWorthGroup,
    NetWorthResponse,
)
from app.services.finance.utils import DEFAULT_CURRENCY


async def get_net_worth_series(
    db: AsyncSession,
    *,
    owner_user_id: int | None = None,
    days: int = 90,
    currency: str = DEFAULT_CURRENCY,
    account_ids: list[int] | None = None,
    without_house: bool = False,
    until: date | None = None,
) -> list[FinanceNetWorthSnapshot]:
    """The net-worth snapshot series (oldest first) — one indexed range scan.

    With ``account_ids`` the series is summed live from the per-account
    balance snapshots instead of the materialized owner-level rows, so a
    filtered Overview can chart just the accounts in view. The join back to
    ``finance_account`` keeps the owner scope authoritative: an id from
    another owner contributes nothing. ``without_house`` leaves the
    property accounts and the loans they secure out, the same way;
    ``until`` stops the series at that day (a from-to range, #342).
    """
    since = _today() - timedelta(days=max(days, 1) - 1)
    if account_ids is not None or without_house:
        left_out = await _left_out(db, owner_user_id, without_house)
        rows = await _counted_rows(
            db, owner_user_id, account_ids, left_out, since, until
        )
        per_day: dict[date, list[int]] = {}
        for balance_date, classification, _type, total in rows:
            sum_into(per_day.setdefault(balance_date, [0, 0]), classification, total)
        # Transient rows, shaped like the materialized ones; never persisted.
        return [
            FinanceNetWorthSnapshot(
                owner_user_id=owner_user_id,
                as_of_date=day,
                total_assets_amount=assets,
                total_liabilities_amount=liabilities,
                net_worth_amount=assets - liabilities,
                currency=currency,
            )
            for day, (assets, liabilities) in sorted(per_day.items())
        ]

    return await queries.net_worth_series_since(
        db, owner_user_id=owner_user_id, since=since, until=until, currency=currency
    )


async def _left_out(
    db: AsyncSession, owner_user_id: int | None, without_house: bool
) -> set[int]:
    """The property accounts and the loans they secure, when the house is
    left out (#343); nothing otherwise."""
    if not without_house:
        return set()
    return await queries.house_account_ids(db, owner_user_id=owner_user_id)


async def _counted_rows(
    db: AsyncSession,
    owner_user_id: int | None,
    account_ids: list[int] | None,
    left_out: set[int],
    since: date,
    until: date | None,
) -> list[tuple[date, str, str, int]]:
    """(day, classification, account type, balance) for ``account_ids``
    (None: the whole book) less ``left_out``, each day only while the
    account counts in net worth then (#314, #315) - a closed one stays in
    the days before it closed."""
    accounts = {
        a.id: a
        for a in await queries.live_accounts_for_owner(db, owner_user_id=owner_user_id)
    }
    ids = [
        i
        for i in (account_ids if account_ids is not None else accounts)
        if i is not None and i not in left_out
    ]
    return [
        (day, classification, account_type, int(balance or 0))
        for day, account_id, classification, account_type, balance in (
            await queries.balance_series(
                db,
                account_ids=ids,
                since=since,
                until=until,
                owner_user_id=owner_user_id,
            )
        )
        if account_id in accounts and counts_in_net_worth(accounts[account_id], day)
    ]


async def net_worth_by_type(
    db: AsyncSession,
    *,
    owner_user_id: int | None = None,
    days: int = 90,
    account_ids: list[int] | None = None,
    without_house: bool = False,
    until: date | None = None,
) -> NetWorthByType:
    """Net worth by component (#343): each account group's summed balance
    per day, debts below zero, in the Accounts page's order. Off the same
    per-account snapshots and the same query as the filtered net line, so
    the groups sum to it."""
    since = _today() - timedelta(days=max(days, 1) - 1)
    left_out = await _left_out(db, owner_user_id, without_house)
    rows = await _counted_rows(db, owner_user_id, account_ids, left_out, since, until)
    by_group: dict[str, dict[date, int]] = {}
    for balance_date, classification, account_type, total in rows:
        amount = int(total or 0)
        signed = -abs(amount) if classification == "liability" else amount
        day = by_group.setdefault(account_group(account_type), {})
        day[balance_date] = day.get(balance_date, 0) + signed
    dates = sorted({day for values in by_group.values() for day in values})
    return NetWorthByType(
        dates=dates,
        groups=[
            NetWorthGroup(
                label=label, values=[by_group[label].get(day, 0) for day in dates]
            )
            for label, _types in ACCOUNT_GROUPS
            if label in by_group
        ],
    )


def analyst_available() -> bool:
    """Whether this build shipped the finance analyst agent.

    The analyst module is pruned entirely from a project generated without the
    AI service, so a failed import is the answer rather than an error: this is
    feature detection, and False is the whole handling. Asking the code beats
    keeping a second record of which capabilities were selected, which would
    only ever drift from the truth.

    Imported inside the function because the analyst imports this module.
    """
    try:
        from app.services.finance.domains.detection import analyst
    except ImportError:
        return False

    return hasattr(analyst, "run_analyst_note")


async def account_rollup(
    db: AsyncSession, *, owner_user_id: int | None = None
) -> tuple[int, int, int]:
    """(assets, liabilities owed, live account count): the accounts in net
    worth (#314) at the balance the pages show - a debt is owed whichever
    sign its bank stores it with."""
    accounts = await queries.live_accounts_for_owner(db, owner_user_id=owner_user_id)
    activity = await queries.transaction_totals_by_account(
        db, owner_user_id=owner_user_id, account_ids=[a.id for a in accounts]
    )
    assets = liabilities = 0
    for account in accounts:
        if not counts_in_net_worth(account):
            continue
        worth = effective_balance(
            current_balance=account.current_balance,
            balance_as_of=account.balance_as_of,
            classification=account.classification,
            activity_balance=activity.get(account.id, 0),
        )
        totals = [assets, liabilities]
        sum_into(totals, account.classification, worth)
        assets, liabilities = totals
    return assets, liabilities, len(accounts)


async def connection_rollup(
    db: AsyncSession, *, owner_user_id: int | None = None
) -> tuple[int, int]:
    """(connection_count, needs_action_count) in a single aggregate query."""
    return await queries.connection_rollup(db, owner_user_id=owner_user_id)


async def asset_liability_totals(
    db: AsyncSession, *, owner_user_id: int | None = None
) -> tuple[int, int]:
    """Live (assets, liabilities) totals summed across visible accounts."""
    assets, liabilities, _ = await account_rollup(db, owner_user_id=owner_user_id)
    return assets, liabilities


async def get_net_worth(
    db: AsyncSession,
    *,
    owner_user_id: int | None = None,
    currency: str = DEFAULT_CURRENCY,
) -> NetWorthResponse:
    assets, liabilities = await asset_liability_totals(db, owner_user_id=owner_user_id)
    return NetWorthResponse(
        net_worth_amount=assets - liabilities,
        total_assets_amount=assets,
        total_liabilities_amount=liabilities,
        currency=currency,
    )


async def get_status_summary(
    db: AsyncSession,
    *,
    owner_user_id: int | None = None,
    currency: str = DEFAULT_CURRENCY,
) -> FinanceStatusSummary:
    """Headline numbers for the dashboard card, health check, and CLI."""
    assets, liabilities, account_count = await account_rollup(
        db, owner_user_id=owner_user_id
    )
    connection_count, _ = await connection_rollup(db, owner_user_id=owner_user_id)
    new_insight_count = await insights.count_new_insights(
        db, owner_user_id=owner_user_id
    )
    return FinanceStatusSummary(
        net_worth_amount=assets - liabilities,
        total_assets_amount=assets,
        total_liabilities_amount=liabilities,
        account_count=account_count,
        connection_count=connection_count,
        new_insight_count=new_insight_count,
        analyst_enabled=analyst_available(),
        currency=currency,
    )


async def health(
    db: AsyncSession, *, owner_user_id: int | None = None
) -> FinanceHealth:
    """Liveness summary: account/connection counts + worst connection state.

    Backs ``GET /api/v1/finance/health``. ``status`` is ``"ok"`` unless a
    connection needs the user's attention (re-auth, consent expired, ...).
    """
    accounts = len(
        await queries.live_accounts_for_owner(db, owner_user_id=owner_user_id)
    )
    connections, needs_action = await connection_rollup(db, owner_user_id=owner_user_id)
    return FinanceHealth(
        status="ok" if needs_action == 0 else "attention",
        accounts=accounts,
        connections=connections,
        connections_needing_action=needs_action,
    )
