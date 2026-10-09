"""The bill reads an agent matches payments with: the bills, and which
transactions could have paid them.

Split from ``ai_write_tools`` at its budget; these were never writes.
"""

from __future__ import annotations

from typing import Any

from app.core.db import get_async_session
from app.services.ai.domains.chat.tools import register_tool
from app.services.finance.schemas.agent_tools import (
    BillCandidates,
    Bills,
    BillWithCandidates,
)


async def bills() -> Bills:
    """Every live bill and income stream. 'amount_cents' is ALWAYS a
    number: the figure the user declared, else the one measured from the
    bill's own payments ('amount_is_declared' says which). 'account' and
    'category' are as the Bills page shows them. The id is what a
    recurring.match proposal's 'stream_id' takes, and what a
    transaction's 'bill_id' names: a payment already the bill's is no
    match candidate.
    """
    # Not the raw expected_amount: null unless typed, 40 bills read "no amount".
    from app.services.finance.domains.ledger.queries.accounts import account_names
    from app.services.finance.domains.planning.recurring import queries, streams

    async with get_async_session() as session:
        rows = await queries.active_streams(session, owner_user_id=None)
        accounts = await account_names(session, [s.account_id for s in rows])
        categories = await streams.stream_category_names(session, {s.id for s in rows})
    return {
        "bills": [
            {
                "id": int(s.id or 0),
                "name": s.name,
                "direction": s.direction,
                "frequency": s.frequency,
                "amount_cents": s.amount,
                "amount_is_declared": s.expected_amount is not None,
                "account": accounts.get(s.account_id),
                "account_id": s.account_id,
                "category": categories.get(s.id),
                "next_expected_date": (
                    s.next_expected_date.isoformat() if s.next_expected_date else None
                ),
                "last_date": s.last_date.isoformat() if s.last_date else None,
            }
            for s in rows
        ]
    }


async def bill_candidates(stream_id: int | None = None) -> BillCandidates:
    """Which unclaimed transactions could be a bill's payment, ranked by
    the app's own matcher (direction, amount band, due-date window, name
    affinity). Without ``stream_id``: every bill past its due date that
    has any - the Bills page's Review queue, in one call. With it: that
    one bill. A candidate's 'id' is what a recurring.match proposal's
    'transaction_id' takes, where the payee is the bill's. Every
    candidate is free to take: one whose 'bill_id' is set sits on a
    detector's unconfirmed guess, and the match moves it. The first
    place to look, not the only one: a payment ``transactions`` finds
    is as good an id.
    """
    from app.services.finance.ai_tools import transaction_rows
    from app.services.finance.domains.planning.recurring.review import review_queue
    from app.services.finance.domains.planning.recurring.streams import get_recurring
    from app.services.finance.service import FinanceService

    async with get_async_session() as session:
        service = FinanceService(session)
        queue: list[tuple[Any, list[Any]]]
        if stream_id is None:
            queue = list(await review_queue(service, None))
        else:
            stream = await get_recurring(session, stream_id, None)
            rows = await service.recurring_match_candidates(stream_id)
            queue = [(stream, rows)] if stream is not None else []
        # One read for every bill's rows, in the shape every tool returns -
        # the payee as you named it ("MVP"), not the bank's "Med Service Corp".
        every = [t for _stream, rows in queue for t in rows]
        payees = await service.merchant_names(
            {t.merchant_id for t in every if t.merchant_id is not None}
        )
        shaped = iter(
            await transaction_rows(
                session, [(t, payees.get(t.merchant_id)) for t in every]
            )
        )
    found: list[BillWithCandidates] = [
        {
            "stream_id": int(stream.id or 0),
            "name": stream.name,
            "due_date": (
                stream.next_expected_date.isoformat()
                if stream.next_expected_date
                else None
            ),
            "candidates": [next(shaped) for _t in rows],
        }
        for stream, rows in queue
    ]
    return {"bills": found}


register_tool(
    "bills",
    bills,
    description="Live bills and income streams with the ids matches need",
    replace=True,
)
register_tool(
    "bill_candidates",
    bill_candidates,
    description="Bills past due with the payments that could be theirs, ranked",
    replace=True,
)
