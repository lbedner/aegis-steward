"""One account, two feeds: a linked bank and a file export (#309).

Quicken exports feed the ledger, and a linked bank can feed the same
account beside them. A charge both bring is one charge. Both rows are
kept - the bank's, so its later edits and removals still find it - but
the export's row is the ``primary`` and the bank's is its ``duplicate``
(``canonical_transaction_id``), which every ledger read already leaves
out. The export wins because it carries the payees and categories
somebody curated; a category set by hand on the bank's row survives it.

The same charge: the same account and amount, both posted, dated within
``SAME_CHARGE_DAYS`` of each other - a bank and an export can date one
charge a day or two apart. Nearest date first, and each row pairs once,
so two real $5 coffees in a week stay two. A pending row waits until it
posts: pairing it, then watching it post as a new row, would count the
charge twice.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping
from datetime import date, timedelta
from typing import Protocol

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.constants import FEED_SOURCES, FILE_SOURCES
from app.services.finance.domains.ledger.queries.transactions import (
    transactions_by_ids,
)
from app.services.finance.domains.ledger.queries.two_feeds import (
    FeedRow,
    unpaired_rows,
)
from app.services.finance.models import FinanceTransaction

SAME_CHARGE_DAYS = 3


class Unpaired:
    """The other feed's rows still waiting for their half, taken nearest
    date first."""

    def __init__(self, rows: Iterable[FeedRow]) -> None:
        self._waiting: dict[tuple[int, int], list[FeedRow]] = defaultdict(list)
        for row in rows:
            self._waiting[(row.account_id, row.amount)].append(row)

    def take(self, account_id: int, when: date, amount: int) -> int | None:
        """The id of the row this charge is, claimed; None when none is."""
        waiting = self._waiting.get((account_id, amount), [])
        nearest = min(
            ((abs((row.date_ - when).days), row.id, row) for row in waiting),
            default=None,
        )
        if nearest is None or nearest[0] > SAME_CHARGE_DAYS:
            return None
        found = nearest[2]
        waiting.remove(found)
        return found.id


async def unpaired(
    db: AsyncSession,
    account_ids: Collection[int],
    sources: Collection[str],
    dates: Collection[date],
) -> Unpaired:
    """The rows from ``sources`` that charges dated ``dates`` could be."""
    if not dates:
        return Unpaired(())
    window = timedelta(days=SAME_CHARGE_DAYS)
    return Unpaired(
        await unpaired_rows(
            db, account_ids, sources, min(dates) - window, max(dates) + window
        )
    )


async def same_charges(
    db: AsyncSession, account_ids: Collection[int], as_one: int
) -> list[tuple[int, int]]:
    """``(export row, bank row)`` for each charge both feeds brought to
    these accounts and not yet paired, read as though they were one
    account (``as_one``) - what merging them would pair. Their whole
    history: a merge is once, not every sync."""
    rows = [
        row._replace(account_id=as_one)
        for row in await unpaired_rows(
            db, account_ids, FILE_SOURCES | FEED_SOURCES, date.min, date.max
        )
    ]
    exported = Unpaired(row for row in rows if row.source in FILE_SOURCES)
    fed = sorted(
        (row for row in rows if row.source in FEED_SOURCES),
        key=lambda r: (r.date_, r.id),
    )
    return [
        (same, row.id)
        for row in fed
        if (same := exported.take(as_one, row.date_, row.amount)) is not None
    ]


# Pairs read and written per round: a first import onto a linked account
# can pair years of charges, and SQLite takes ~32k ids in one IN.
PAIRS_PER_ROUND = 500


async def pair_ids(db: AsyncSession, pairs: Iterable[tuple[int, int]]) -> None:
    """Pair each ``(primary id, duplicate id)``, a round of reads at a time.
    Each pair teaches the payee memory what the bank's wording means: the
    one place every pairing - a sync, an import, a merge - goes through."""
    from app.services.finance.domains.ledger import payee_aliases

    pairs = list(pairs)
    for start in range(0, len(pairs), PAIRS_PER_ROUND):
        chunk = pairs[start : start + PAIRS_PER_ROUND]
        rows = await transactions_by_ids(db, [i for pair_ in chunk for i in pair_])
        for primary, duplicate in chunk:
            pair(rows[primary], rows[duplicate])
        await payee_aliases.learn_from_pairs(
            db, [(rows[primary], rows[duplicate]) for primary, duplicate in chunk]
        )
        await db.flush()


class _Planned(Protocol):
    row_number: int
    same_as: int | None


async def pair_imported(
    db: AsyncSession, rows: Iterable[_Planned], created: Mapping[int, int]
) -> None:
    """An import's inserted rows with the bank rows they are (``same_as``):
    the export's row is the primary. ``created`` maps row number to id."""
    await pair_ids(db, [(created[r.row_number], r.same_as) for r in rows if r.same_as])


def pair(primary: FinanceTransaction, duplicate: FinanceTransaction) -> None:
    """One charge from two feeds: ``primary`` stays in view."""
    primary.dedup_status = "primary"
    duplicate.dedup_status = "duplicate"
    duplicate.canonical_transaction_id = primary.id
    if duplicate.category_source == "user" and primary.category_source != "user":
        primary.category_id = duplicate.category_id
        primary.category_source = "user"
