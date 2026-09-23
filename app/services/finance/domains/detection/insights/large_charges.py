"""A charge is large against what its payee usually charges.

The rule compared each charge with its whole account's median. On a card
of streaming subscriptions that median is about fifteen dollars, so the
AMEX Pay Over Time interest - $931, every month for two years - read as
"unusually large" every month: its stream had been deleted in a cleanup,
which unlinked it, and the rule's only idea of "recurring" was a live
stream link. Measured on the real ledger 2026-09-22: 27 of 42 open
large-charge alerts were a payee charging what it always charges.

So the payee's own history answers first: the same merchant on any
account (a merchant is an identity, and a bill that moved cards brings
its history), or the same description on THIS account (a description is
not an identity across accounts - every check is "check"). One earlier
charge is enough, because a yearly renewal is seen once a year. Only a
payee with no history falls back to the account's norm.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
import statistics
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.detection import queries
from app.services.finance.domains.detection.insights.formatting import format_usd
from app.services.finance.domains.detection.recurring.cadence import _descriptor_key
from app.services.finance.models import FinanceTransaction

# large_transaction: an outlier is judged against its OWN account, because a
# normal charge on a grocery card and a normal charge on a mortgage account are
# nothing alike. The floors keep a quiet account from crying wolf over an
# ordinary purchase that happens to beat its small median.
LARGE_TXN_WINDOW_DAYS = 35  # how far back to look for candidates
LARGE_TXN_BASELINE_DAYS = 90  # the account's own recent norm
LARGE_TXN_MIN_BASELINE = 10  # peers needed before the median is trusted
LARGE_TXN_MULTIPLE = 4  # x the account's median outflow
LARGE_TXN_CRITICAL_MULTIPLE = 10  # x the median -> critical, not warning
LARGE_TXN_FLOOR = 20_000  # cents; never alert below this
LARGE_TXN_THIN_FLOOR = 50_000  # cents; the only test when history is thin
# A payee's norm: how far back to look (a yearly charge must still find
# last year's), and how far above its usual a charge has to be to stand out.
PAYEE_HISTORY_DAYS = 400
PAYEE_MULTIPLE = 2


def _described(txn: Any) -> str:
    """The description key the recurring detector groups by."""
    return _descriptor_key(
        txn.merchant_name or txn.original_description or txn.name or ""
    )


@dataclass
class PayeeNorms:
    """Every outflow in the history window, indexed by who charged it."""

    by_merchant: dict[int, list[Any]] = field(default_factory=lambda: defaultdict(list))
    by_description: dict[tuple[int, str], list[Any]] = field(
        default_factory=lambda: defaultdict(list)
    )

    @classmethod
    async def load(
        cls, db: AsyncSession, owner_user_id: int | None, today: date
    ) -> PayeeNorms:
        """One query for the whole run, never one per candidate."""
        since = today - timedelta(days=PAYEE_HISTORY_DAYS + LARGE_TXN_WINDOW_DAYS)
        rows = await queries.transaction_rows_where(
            db,
            [
                queries.owner_clause(FinanceTransaction.owner_user_id, owner_user_id),
                FinanceTransaction.deleted_at.is_(None),
                FinanceTransaction.dedup_status != "duplicate",
                FinanceTransaction.amount < 0,
                FinanceTransaction.date_ >= since,
            ],
        )
        norms = cls()
        for row in rows:
            if row.merchant_id is not None:
                norms.by_merchant[row.merchant_id].append(row)
            norms.by_description[(row.account_id, _described(row))].append(row)
        return norms

    def usual(self, txn: Any) -> float | None:
        """This payee's median charge before ``txn``, or None if it has
        charged nothing before - then the account's norm has to do."""
        since = txn.date_ - timedelta(days=PAYEE_HISTORY_DAYS)
        candidates = list(
            self.by_description.get((txn.account_id, _described(txn)), [])
        )
        if txn.merchant_id is not None:
            candidates += self.by_merchant.get(txn.merchant_id, [])
        earlier = {
            row.id: abs(row.amount)
            for row in candidates
            if row.id != txn.id and since <= row.date_ < txn.date_
        }
        return statistics.median(earlier.values()) if earlier else None


async def _create(db: AsyncSession, **fields: Any) -> Any:
    """``create_insight_if_new``, imported late: ``rules`` imports this module."""
    from app.services.finance.domains.detection.insights.rules import (
        create_insight_if_new,
    )

    return await create_insight_if_new(db, **fields)


async def _large_transactions(
    db: AsyncSession,
    owner_user_id: int | None,
    store_owner: int,
    live_accounts,
    today: date,
) -> int:
    """One charge far outside its payee's norm, else its account's.

    Recurring members are excluded on purpose: a mortgage payment is large
    every month, and streams already have their own rule (``price_hike``).
    Candidates are limited to a recent window so a first run against years of
    imported history doesn't dump a hundred alerts about ancient purchases.
    """
    rows = await queries.transaction_rows_where(
        db,
        [
            queries.owner_clause(FinanceTransaction.owner_user_id, owner_user_id),
            FinanceTransaction.deleted_at.is_(None),
            FinanceTransaction.dedup_status != "duplicate",
            FinanceTransaction.excluded_from_reports.is_(False),
            FinanceTransaction.is_transfer.is_(False),
            FinanceTransaction.recurring_stream_id.is_(None),
            FinanceTransaction.amount < 0,
            FinanceTransaction.date_ >= today - timedelta(days=LARGE_TXN_BASELINE_DAYS),
            FinanceTransaction.account_id.in_(live_accounts),
        ],
    )

    by_account: dict[int, list[FinanceTransaction]] = {}
    for txn in rows:
        by_account.setdefault(txn.account_id, []).append(txn)

    candidate_start = today - timedelta(days=LARGE_TXN_WINDOW_DAYS)
    norms = await PayeeNorms.load(db, owner_user_id, today)
    created = 0
    for txns in by_account.values():
        peer_amounts = {txn.id: abs(txn.amount) for txn in txns}
        for txn in txns:
            if txn.date_ < candidate_start:
                continue  # baseline only
            amount = abs(txn.amount)
            # A transaction is never its own baseline.
            peers = [
                value for txn_id, value in peer_amounts.items() if txn_id != txn.id
            ]
            usual = norms.usual(txn)
            if usual is not None:
                # The payee has a history, so it is the norm - not a card
                # of fifteen-dollar subscriptions it happens to share.
                if amount < PAYEE_MULTIPLE * usual:
                    continue
                # Still large in absolute terms: double a $15 coffee is not
                # a large charge (22 of those appeared the one run this
                # floor was missing).
                threshold = LARGE_TXN_FLOOR
                critical_at = usual * LARGE_TXN_CRITICAL_MULTIPLE
                body = (
                    f"{txn.name or 'A charge'} on {txn.date_} was {format_usd(amount)}, "
                    f"well above the usual {format_usd(int(usual))} from this payee."
                )
            elif len(peers) >= LARGE_TXN_MIN_BASELINE:
                median_peer = statistics.median(peers)
                threshold = max(LARGE_TXN_FLOOR, int(median_peer * LARGE_TXN_MULTIPLE))
                critical_at = median_peer * LARGE_TXN_CRITICAL_MULTIPLE
                body = (
                    f"{txn.name or 'A charge'} on {txn.date_} was {format_usd(amount)}, "
                    f"well above the usual {format_usd(int(median_peer))} on this account."
                )
            else:
                threshold = LARGE_TXN_THIN_FLOOR
                critical_at = None
                body = (
                    f"{txn.name or 'A charge'} on {txn.date_} was {format_usd(amount)}, "
                    "unusually large for this account."
                )
            if amount < threshold:
                continue
            severity = (
                "critical"
                if critical_at is not None and amount >= critical_at
                else "warning"
            )
            if await _create(
                db,
                owner_user_id=store_owner,
                insight_type="large_transaction",
                dedup_key=f"large_txn:{txn.id}",
                severity=severity,
                title=f"Large charge: {format_usd(amount)}",
                body=body,
                detected_amount=txn.amount,
                related_transaction_id=txn.id,
                related_account_id=txn.account_id,
                related_category_id=txn.category_id,
            ):
                created += 1
    await _retract_false_alarms(db, store_owner, norms)
    return created


async def _retract_false_alarms(
    db: AsyncSession, store_owner: int, norms: PayeeNorms
) -> None:
    """Take back what the old account-median norm raised about a payee
    charging its usual. Never a resolved one: the person's words make it
    a record, and it is off every open list already."""
    from app.services.finance.models import FinanceInsight

    alerts = await queries.insight_rows_where(
        db,
        [
            FinanceInsight.owner_user_id == store_owner,
            FinanceInsight.insight_type == "large_transaction",
            FinanceInsight.status == "new",
            FinanceInsight.resolution.is_(None),
            FinanceInsight.related_transaction_id.is_not(None),
        ],
    )
    charges = (
        {
            row.id: row
            for row in await queries.transaction_rows_where(
                db,
                [FinanceTransaction.id.in_([a.related_transaction_id for a in alerts])],
            )
        }
        if alerts
        else {}
    )
    from app.services.finance.domains.writes.findings import awaiting_resolution

    deciding = await awaiting_resolution(db)
    stale = []
    for alert in alerts:
        charge = charges.get(alert.related_transaction_id)
        if charge is None or alert.id in deciding:
            continue
        usual = norms.usual(charge)
        if usual is not None and abs(charge.amount) < PAYEE_MULTIPLE * usual:
            stale.append(alert)
    for alert in stale:
        await db.delete(alert)
    if stale:
        await db.flush()
