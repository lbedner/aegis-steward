"""The stream rules: a mature stream's expected charge never showed up,
and a stream detection just found.

The first is the largest of the rules and the only one reasoning about
absence - it must establish that a stream was due, that it was mature
enough to trust, and only then that nothing paid it.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlmodel import or_
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.constants import frequency_label
from app.services.finance.domains.detection import queries
from app.services.finance.domains.detection.insights.commitments import (
    is_commitment,
    not_paused_clause,
    stream_staleness,
)
from app.services.finance.domains.detection.insights.formatting import (
    format_usd,
)
from app.services.finance.domains.detection.insights.rules.shared import (
    create_insight_if_new,
)
from app.services.finance.domains.planning.recurring import queries as recurring_queries
from app.services.finance.models import (
    FinanceRecurringStream,
)

# The insight a newly detected bill raises: Confirm or Mute it (#340).
NEW_BILL = "new_recurring"


async def _missed_recurring(
    db: AsyncSession,
    store_owner: int,
    live_accounts,
    today: date,
    floor: date | None = None,
) -> int:
    """A mature stream whose expected charge never arrived.

    Runs after ``detect_recurring``, which advances ``next_expected_date`` when
    a charge lands - so a stream still pointing at a past due date with nothing
    newer than that date is genuinely overdue, not merely stale.
    """
    streams = await queries.stream_rows_where(
        db,
        [
            FinanceRecurringStream.owner_user_id == store_owner,
            FinanceRecurringStream.deleted_at.is_(None),
            FinanceRecurringStream.status == "mature",
            FinanceRecurringStream.is_active.is_(True),
            FinanceRecurringStream.is_muted.is_(False),
            not_paused_clause(today),
            FinanceRecurringStream.next_expected_date.is_not(None),
            or_(
                FinanceRecurringStream.account_id.is_(None),
                FinanceRecurringStream.account_id.in_(live_accounts),
            ),
        ],
    )
    # Retraction sweep, batched (one query, never one per stream). An
    # alert stops describing a missed bill when its stream has since:
    # - RECOVERED: the payment landed with a later import ("fresh" with a
    #   last_date), so "hasn't been paid" is now false; or
    # - LEFT THE FETCH: paused, muted, deactivated or deleted - the user
    #   said stop expecting this, and the stream never re-enters this
    #   rule to clear its own leftovers.
    # Left alone, either kind sits "new" forever, telling every reader
    # (and every AI briefing) the bill is still missed.
    fetched_by_id = {s.id: s for s in streams if s.id is not None}
    open_alerts = await queries.retractable_alerts(db, store_owner)
    retracted_any = False
    for alert in open_alerts:
        stream = fetched_by_id.get(alert.related_stream_id)
        if stream is None:
            retract = True  # paused / muted / deactivated / deleted
        else:
            retract = (
                stream.last_date is not None
                and stream_staleness(stream, today, floor) == "fresh"
            )
        if retract:
            await db.delete(alert)
            retracted_any = True
    if retracted_any:
        await db.flush()

    if not streams:
        return 0

    # A recurring INTERNAL TRANSFER (a monthly checking->savings sweep) ticks
    # like a bill, but "your transfer hasn't been paid" is not an alert -
    # money you move between your own accounts is not owed to anyone. A
    # stream with any transfer-flagged member is a transfer rhythm; skip it.
    transfer_stream_ids = await recurring_queries.transfer_flagged_stream_ids(
        db, [s.id for s in streams]
    )

    # Payee lifelines: the latest arrival per (payee, direction) across ALL
    # accounts. A commitment that migrates accounts (a card bill moved to
    # checking) leaves its old stream permanently "overdue" - the lifeline
    # proves the bill is alive elsewhere, so the old stream is superseded,
    # not missed.
    payee_lifelines: dict[tuple[str, str], date] = {}
    for candidate in streams:
        if candidate.last_date is None:
            continue
        key = (candidate.normalized_payee, candidate.direction)
        current = payee_lifelines.get(key)
        if current is None or candidate.last_date > current:
            payee_lifelines[key] = candidate.last_date

    created = 0
    for stream in streams:
        if stream.id in transfer_stream_ids:
            # An internal-transfer rhythm, not an obligation. Pairing can
            # land AFTER an earlier pass already alerted (a multi-file
            # import sees one leg before the other), so also retract any
            # alert that pass created - it was wrong, not merely stale.
            stale = await queries.retractable_alerts(db, store_owner, stream.id)
            for insight in stale:
                await db.delete(insight)
            if stale:
                await db.flush()
            continue
        inflow = stream.direction == "inflow"
        if not inflow and not is_commitment(stream):
            continue  # a merchant-visit rhythm, not a commitment
        due = stream.next_expected_date
        lifeline = payee_lifelines.get((stream.normalized_payee, stream.direction))
        if (
            stream_staleness(stream, today, floor) == "overdue"
            and due is not None
            and lifeline is not None
            and lifeline >= due
        ):
            # Superseded, not missed: the same commitment kept arriving on
            # another account after this stream's due date (an overdue
            # stream's own last_date is < due, so the lifeline is a
            # sibling's). Like the transfer case above, a later pass can
            # learn this AFTER an earlier pass already alerted, so also
            # retract - the alert was wrong.
            stale = await queries.retractable_alerts(db, store_owner, stream.id)
            for insight in stale:
                await db.delete(insight)
            if stale:
                await db.flush()
            continue
        if stream_staleness(stream, today, floor) != "overdue":
            # "fresh" (not due yet, or arrived - retracted in the batch
            # below) or "stale" (a zombie stream out of imported history -
            # a cancelled subscription, a closed account, not a live bill
            # that just went missing) - only "overdue" is worth an alert.
            continue
        amount = stream.amount
        title = (
            f"{stream.name} hasn't arrived"
            if inflow
            else f"{stream.name} hasn't been paid"
        )
        if await create_insight_if_new(
            db,
            owner_user_id=store_owner,
            insight_type="missed_recurring",
            # Keyed to the due date, so the next missed cycle alerts again.
            dedup_key=f"missed:{stream.id}:{due.isoformat()}",
            severity="critical" if inflow else "warning",
            title=title,
            body=(
                f"{stream.name} was expected on {due} ({format_usd(amount)}) and "
                "has not shown up."
            ),
            detected_amount=amount,
            related_stream_id=stream.id,
            related_account_id=stream.account_id,
            related_category_id=stream.category_id,
        ):
            created += 1
    return created


# How long a detected bill stays news: past this it is simply waiting in
# Bills' Detected tab, and the first pass after this shipped must not
# announce every proposal ever made.
NEW_BILL_DAYS = 14


async def _new_bills(db: AsyncSession, store_owner: int, today: date) -> int:
    """A bill detection just found, announced rather than only counted in
    the Detected tab, so a forgotten subscription is caught the week it
    appears (#340). Taken back once it is confirmed, muted or gone."""
    open_alerts = await queries.retractable_alerts(
        db, store_owner, insight_type=NEW_BILL
    )
    proposed = {
        s.id: s
        for s in await queries.stream_rows_where(
            db, queries.proposal_clauses(store_owner)
        )
        if s.deleted_at is None and not s.is_muted
    }
    for alert in open_alerts:
        if alert.related_stream_id not in proposed:
            await db.delete(alert)
    since = today - timedelta(days=NEW_BILL_DAYS)
    created = 0
    for stream in proposed.values():
        if stream.created_at.date() < since:
            continue
        # A price that moves is shopping (Target, CVS) or a utility, and
        # waits in Detected; news is income or a fixed charge - the
        # forgotten subscription this is for.
        if stream.direction == "outflow" and stream.amount_is_variable:
            continue
        noun = "income" if stream.direction == "inflow" else "bill"
        if await create_insight_if_new(
            db,
            owner_user_id=store_owner,
            insight_type=NEW_BILL,
            dedup_key=f"{NEW_BILL}:{stream.id}",
            severity="info",
            title=f"New {noun}: {stream.name}",
            body=(
                f"{frequency_label(stream.frequency)}, "
                f"{format_usd(stream.amount)}; next expected "
                f"{stream.next_expected_date or 'not yet known'}."
            ),
            detected_amount=stream.amount,
            related_stream_id=stream.id,
            related_account_id=stream.account_id,
        ):
            created += 1
    await db.flush()
    return created
