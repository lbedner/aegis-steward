"""Bills as the Bills page lists them, and which of them wait on a match.

Moved down from the API and the web routes: the assistant's
``bill_candidates`` walks the same Review queue as the Bills page, and a
service may not import a router to get it.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.services.finance.domains.detection.insights.commitments import (
    is_commitment,
    is_paused,
    stream_staleness,
)
from app.services.finance.domains.ledger.queries.accounts import (
    account_names as named_accounts,
)
from app.services.finance.models import FinanceRecurringStream, FinanceTransaction
from app.services.finance.schemas import RecurringStreamResponse
from app.services.finance.service import FinanceService
from app.services.finance.service.accounts import HOUSEHOLD
from app.services.finance.utils import current_date

REVIEW_LIMIT = 50


async def visible_streams(
    service: FinanceService,
    owner_user_id: int | None,
    subject_id: int | None = HOUSEHOLD,
) -> tuple[list[FinanceRecurringStream], set[int]]:
    """Streams worth listing, plus which of them are card/loan payments.

    Streams whose members are internal-transfer legs (a monthly card
    autopay) are excluded entirely - they are money moved, not bills, and
    one of them can inflate the rollup by a five-figure fiction. Payment
    streams are the carve-out: they stay VISIBLE (a payment has to be
    confirmable here or it can never reach the cash forecast) but out of
    the rollup, because the card's swipes already counted.
    """
    streams = await service.list_recurring(
        owner_user_id=owner_user_id, subject_id=subject_id
    )
    transfer_ids = await service.transfer_stream_ids([s.id for s in streams])
    payment_ids = await service.payment_stream_ids(list(transfer_ids))
    streams = [s for s in streams if s.id not in transfer_ids or s.id in payment_ids]
    return streams, payment_ids


async def hydrate_streams(
    service: FinanceService,
    streams: list[FinanceRecurringStream],
    owner_user_id: int | None,
    payment_ids: set[int] | None = None,
) -> list[RecurringStreamResponse]:
    """Stream rows as full responses: display names, icon, health.

    One query per lookup for the whole batch, shared by the list endpoint
    and any single-row re-render (the web frontend answers a row action
    with the row). ``payment_ids`` is what ``visible_streams`` computed;
    a single-row caller lets it be looked up here.
    """
    from app.core.config import settings
    from app.services.finance.domains.ledger.merchant_icon import payee_icons

    if payment_ids is None:
        transfer_ids = await service.transfer_stream_ids([s.id for s in streams])
        payment_ids = await service.payment_stream_ids(list(transfer_ids))
    # Display names in one query each, so the Bills & Income table can show
    # where a stream draws from and what it is filed under.
    category_names = await service.stream_category_names({s.id for s in streams})
    # A stream that has been attributed to a payee takes the PAYEE's name
    # for its icon, not its own: the stream name is whatever descriptor the
    # detector last saw ("YOUTUBEPREMI G.CO/HELPPAY# CA XXXX3007"), while
    # the payee is the thing that actually has a brand ("Google"). Falls
    # back to the stream name for anything not yet named.
    payee_names = await service.merchant_names(
        {s.merchant_id for s in streams if s.merchant_id is not None}
    )
    account_names = await named_accounts(service.db, [s.account_id for s in streams])
    icons = await payee_icons(
        service.db,
        [(s.merchant_id, payee_names.get(s.merchant_id) or s.name) for s in streams],
    )
    # Same lookback floor generate_insights computes for _missed_recurring -
    # a stream reading "stale" here is exactly the set that rule already
    # treats as a zombie rather than a live bill (see stream_staleness).
    today = current_date()
    floor = (
        today - timedelta(days=settings.FINANCE_RULES_LOOKBACK_DAYS)
        if settings.FINANCE_RULES_LOOKBACK_DAYS
        else None
    )
    return [
        RecurringStreamResponse.from_row(
            s,
            account_name=account_names.get(s.account_id),
            category_name=category_names.get(s.id),
            icon=icons.get(payee_names.get(s.merchant_id) or s.name),
            staleness=stream_staleness(s, today, floor),
            is_payment=s.id in payment_ids,
        )
        for s in streams
    ]


def past_due(stream: RecurringStreamResponse, today: date) -> bool:
    return stream.next_expected_date is not None and stream.next_expected_date < today


def needs_review(stream: RecurringStreamResponse, today: date) -> bool:
    """A curated bill whose due date has passed and is still worth chasing
    (no grace window: Review is the user asking "what has passed?")."""
    return (
        stream.direction == "outflow"
        and is_commitment(stream)
        and not stream.is_muted
        and not is_paused(stream, today)
        and stream.staleness != "stale"
        and past_due(stream, today)
    )


async def review_queue(
    service: FinanceService, owner_user_id: int | None
) -> list[tuple[RecurringStreamResponse, list[FinanceTransaction]]]:
    """The overdue curated bills that have at least one candidate payment,
    each with its shortlist - the Bills page's Review and the assistant's
    ``bill_candidates``, so "what is left to match" has one answer."""
    streams, payment_ids = await visible_streams(service, owner_user_id)
    today = current_date()
    queue: list[tuple[RecurringStreamResponse, list[FinanceTransaction]]] = []
    for stream in await hydrate_streams(service, streams, owner_user_id, payment_ids):
        if len(queue) >= REVIEW_LIMIT:
            break
        if not needs_review(stream, today):
            continue
        rows = await service.recurring_match_candidates(
            stream.id, owner_user_id=owner_user_id
        )
        if rows:
            queue.append((stream, rows))
    return queue
