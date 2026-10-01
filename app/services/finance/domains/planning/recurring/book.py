"""The streams the budget header counts, read once.

The header, its popups, its outlook and the goal figures each used to
list the streams and drop the transfers themselves, in two different
orders; one read keeps them counting the same set.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.planning.recurring.streams import (
    in_account_scope,
    list_recurring,
    transfer_stream_ids,
)
from app.services.finance.models import FinanceRecurringStream


@dataclass(frozen=True)
class StreamBook:
    """Every live stream, and which of them are transfers: the one read
    the budget header, its popups, its outlook and the goal figures
    share, so no two of them count a different set of streams."""

    streams: list[FinanceRecurringStream]
    transfer_ids: frozenset[int]

    def counted(
        self, account_ids: list[int] | None = None
    ) -> list[FinanceRecurringStream]:
        """What the header counts: no transfers, inside the account scope."""
        return [
            stream
            for stream in self.streams
            if stream.id not in self.transfer_ids
            and in_account_scope(stream, account_ids)
        ]


async def stream_book(db: AsyncSession, *, owner_user_id: int | None) -> StreamBook:
    streams = await list_recurring(db, owner_user_id=owner_user_id)
    transfer_ids = await transfer_stream_ids(db, [s.id for s in streams])
    return StreamBook(streams, frozenset(transfer_ids))
