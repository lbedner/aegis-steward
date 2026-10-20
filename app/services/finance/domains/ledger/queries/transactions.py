"""Reads for the register: transactions, their splits, their tags.

The paging and search reads are the ones that grow: they carry the
filter fragments, the dedup lookup, and the payee/amount roll-ups the
transaction surfaces are built from.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

from sqlalchemy import func
from sqlmodel import or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger.queries.filters import (
    Narrowing,
    live_account_ids,
    money_moved_in,
    not_duplicate,
    split_aware_category_clause,
    transaction_search_filter,
    uncategorized_catchall_ids,
)
from app.services.finance.models import (
    FinanceAccount,
    FinanceCategory,
    FinanceMerchant,
    FinanceTag,
    FinanceTransaction,
    FinanceTransactionSplit,
    FinanceTransactionTag,
)
from app.services.shared.queries import owner_filters


async def dedup_match(
    db: AsyncSession,
    *,
    account_id: int,
    source: str,
    external_id: str | None = None,
    import_hash: str | None = None,
) -> FinanceTransaction | None:
    """The two-lane dedup match, or None. LANE 1 keys on
    ``(account_id, source, external_id)``; LANE 2 on
    ``(account_id, import_hash)``. Soft-deleted rows release the key."""
    query = select(FinanceTransaction).where(
        FinanceTransaction.account_id == account_id,
        FinanceTransaction.deleted_at.is_(None),
    )
    if external_id is not None:
        query = query.where(
            FinanceTransaction.source == source,
            FinanceTransaction.external_id == external_id,
        )
    elif import_hash is not None:
        query = query.where(FinanceTransaction.import_hash == import_hash)
    else:
        return None
    return (await db.exec(query)).first()


async def transaction_by_id(
    db: AsyncSession, transaction_id: int, *, owner_user_id: int | None = None
) -> FinanceTransaction | None:
    query = select(FinanceTransaction).where(
        FinanceTransaction.id == transaction_id,
        FinanceTransaction.deleted_at.is_(None),
    )
    query = query.where(*owner_filters(FinanceTransaction.owner_user_id, owner_user_id))
    return (await db.exec(query)).first()


async def transactions_by_ids(
    db: AsyncSession, ids: list[int]
) -> dict[int, FinanceTransaction]:
    if not ids:
        return {}
    rows = (
        await db.exec(select(FinanceTransaction).where(FinanceTransaction.id.in_(ids)))
    ).all()
    return {t.id: t for t in rows}


async def live_transactions_by_ids(
    db: AsyncSession, ids: list[int], *, owner_user_id: int | None = None
) -> list[FinanceTransaction]:
    """Not-deleted rows among ``ids``, owner-scoped when given."""
    query = select(FinanceTransaction).where(
        FinanceTransaction.id.in_(ids),
        FinanceTransaction.deleted_at.is_(None),
    )
    query = query.where(*owner_filters(FinanceTransaction.owner_user_id, owner_user_id))
    return list((await db.exec(query)).all())


async def transactions_page(
    db: AsyncSession,
    *,
    owner_user_id: int | None = None,
    account_id: int | None = None,
    account_ids: list[int] | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    category_id: int | None = None,
    merchant_id: int | None = None,
    without_merchant: bool = False,
    uncategorized: bool = False,
    tag_id: int | None = None,
    query: str | None = None,
    include_transfers: bool = False,
    page: int = 1,
    page_size: int = 20,
    amount: int | None = None,
    sort: str | None = None,
    descending: bool = True,
    narrowing: Narrowing | None = None,
) -> tuple[list[FinanceTransaction], int]:
    """The register page + total count (two statements).

    ``sort`` names a COLUMN THE READER CAN SEE, and each sorts by what
    that column prints rather than by the id behind it: ordering payees
    or categories by foreign key would look like nothing at all. Newest
    first stays the default, and every order falls back to it so a page
    boundary cannot land mid-tie and repeat or skip a row."""
    filters = [
        FinanceTransaction.deleted_at.is_(None),
        not_duplicate(),
        FinanceTransaction.account_id.in_(live_account_ids()),
    ]
    if not include_transfers:
        filters.append(FinanceTransaction.is_transfer.is_(False))
    filters.extend(owner_filters(FinanceTransaction.owner_user_id, owner_user_id))
    if account_id is not None:
        filters.append(FinanceTransaction.account_id == account_id)
    if account_ids is not None:
        filters.append(FinanceTransaction.account_id.in_(account_ids))
    if from_date is not None:
        filters.append(FinanceTransaction.date_ >= from_date)
    if to_date is not None:
        filters.append(FinanceTransaction.date_ <= to_date)
    if category_id is not None:
        filters.append(
            split_aware_category_clause(
                FinanceTransaction.category_id == category_id,
                FinanceTransactionSplit.category_id == category_id,
            )
        )
    if merchant_id is not None:
        filters.append(FinanceTransaction.merchant_id == merchant_id)
    if without_merchant:
        filters.append(FinanceTransaction.merchant_id.is_(None))
    if uncategorized:
        filters.extend(uncategorized_clauses())
    if tag_id is not None:
        filters.append(
            FinanceTransaction.id.in_(
                select(FinanceTransactionTag.transaction_id).where(
                    FinanceTransactionTag.tag_id == tag_id
                )
            )
        )
    if query:
        filters.append(transaction_search_filter(query))
    if narrowing is not None:
        filters.extend(narrowing.clauses())
    if amount is not None:
        # By MAGNITUDE: a receipt says $8.00, not -800, and the sign is
        # the ledger's business rather than the reader's. This is the
        # filter that turns "which charge was the $8.00 Target one" from
        # a page of ledger into a row. Whole dollars are how a bill is
        # SAID ("MVP, $970" for $970.44), so they match within the dollar
        # either side; cents given are cents meant.
        cents = abs(amount)
        slack = 99 if cents % 100 == 0 else 0
        filters.append(
            func.abs(FinanceTransaction.amount).between(cents - slack, cents + slack)
        )
    count_query = select(func.count()).select_from(FinanceTransaction).where(*filters)
    total = (await db.exec(count_query)).one()
    query_obj = select(FinanceTransaction).where(*filters)
    # The sortable columns and what each one is ordered BY. Name columns
    # are joined so they sort alphabetically, which is what the reader
    # sees; an outer join, because a row with no payee or no category
    # still belongs on the page.
    if sort == "account":
        query_obj = query_obj.outerjoin(
            FinanceAccount, FinanceAccount.id == FinanceTransaction.account_id
        )
        column = FinanceAccount.name
    elif sort == "category":
        query_obj = query_obj.outerjoin(
            FinanceCategory, FinanceCategory.id == FinanceTransaction.category_id
        )
        column = FinanceCategory.name
    elif sort == "payee":
        query_obj = query_obj.outerjoin(
            FinanceMerchant, FinanceMerchant.id == FinanceTransaction.merchant_id
        )
        column = func.coalesce(
            FinanceMerchant.name,
            FinanceTransaction.merchant_name,
            FinanceTransaction.name,
        )
    elif sort == "amount":
        column = FinanceTransaction.amount
    else:
        column = FinanceTransaction.date_
    ordering = column.desc() if descending else column.asc()
    query_obj = (
        query_obj.order_by(ordering, FinanceTransaction.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list((await db.exec(query_obj)).all()), total


async def transactions_window_with_payees(
    db: AsyncSession,
    *,
    owner_user_id: int | None = None,
    from_date: date | None = None,
    limit: int = 1000,
) -> tuple[list[tuple[FinanceTransaction, str | None]], int]:
    """A date-windowed register slice with the CURATED payee resolved.

    One LEFT JOIN to ``finance_merchant`` (no second query, no ``IN``):
    each row pairs the transaction with the user-curated merchant name -
    the name the register displays - or ``None`` when no merchant is
    assigned. A renamed payee that only lived in the curation layer was
    invisible to consumers reading the raw descriptor columns.
    """
    filters = [
        FinanceTransaction.deleted_at.is_(None),
        not_duplicate(),
        FinanceTransaction.account_id.in_(live_account_ids()),
        FinanceTransaction.is_transfer.is_(False),
    ]
    filters.extend(owner_filters(FinanceTransaction.owner_user_id, owner_user_id))
    if from_date is not None:
        filters.append(FinanceTransaction.date_ >= from_date)
    count_query = select(func.count()).select_from(FinanceTransaction).where(*filters)
    total = (await db.exec(count_query)).one()
    query = (
        select(FinanceTransaction, FinanceMerchant.name)
        .join(
            FinanceMerchant,
            FinanceMerchant.id == FinanceTransaction.merchant_id,
            isouter=True,
        )
        .where(*filters)
        .order_by(FinanceTransaction.date_.desc(), FinanceTransaction.id.desc())
        .limit(limit)
    )
    rows = (await db.exec(query)).all()
    return [(txn, name) for txn, name in rows], total


def uncategorized_clauses() -> list[Any]:
    """What "uncategorized" means everywhere it is asked: an unsplit row
    with no category, or one filed under a catch-all - a row a split
    covers is categorised by its parts, whatever its own column says."""
    return [
        FinanceTransaction.excluded_from_reports.is_(False),
        FinanceTransaction.is_split.is_(False),
        or_(
            FinanceTransaction.category_id.is_(None),
            FinanceTransaction.category_id.in_(uncategorized_catchall_ids()),
        ),
    ]


async def uncategorized_page(
    db: AsyncSession,
    *,
    owner_user_id: int | None = None,
    limit: int | None = 7,
    query: str | None = None,
    from_date: date | None = None,
    account_ids: list[int] | None = None,
) -> tuple[list[FinanceTransaction], int]:
    """Rows nothing has classified (NULL category or a source app's own
    catch-all bucket), newest first, plus a count (two statements)."""
    if account_ids is not None and not account_ids:
        return [], 0
    filters = [
        FinanceTransaction.deleted_at.is_(None),
        not_duplicate(),
        FinanceTransaction.account_id.in_(live_account_ids()),
        *uncategorized_clauses(),
    ]
    filters.extend(owner_filters(FinanceTransaction.owner_user_id, owner_user_id))
    if query:
        filters.append(transaction_search_filter(query))
    if account_ids is not None:
        filters.append(FinanceTransaction.account_id.in_(account_ids))
    if from_date is not None:
        filters.append(FinanceTransaction.date_ >= from_date)

    total = (
        await db.exec(
            select(func.count()).select_from(FinanceTransaction).where(*filters)
        )
    ).one()
    select_query = (
        select(FinanceTransaction)
        .where(*filters)
        .order_by(FinanceTransaction.date_.desc())
    )
    if limit is not None:
        select_query = select_query.limit(limit)
    rows = (await db.exec(select_query)).all()
    return list(rows), int(total or 0)


async def newest_transaction_date(
    db: AsyncSession, account_id: int, *, owner_user_id: int | None = None
) -> date | None:
    """The last day this account saw anything (``money_moved_in``)."""
    filters = money_moved_in(account_id, owner_user_id)
    return (
        await db.exec(select(func.max(FinanceTransaction.date_)).where(*filters))
    ).one()


async def tag_by_normalized_name(
    db: AsyncSession, *, store_owner: int, normalized: str
) -> FinanceTag | None:
    return (
        await db.exec(
            select(FinanceTag).where(
                FinanceTag.owner_user_id == store_owner,
                FinanceTag.normalized_name == normalized,
                FinanceTag.deleted_at.is_(None),
            )
        )
    ).first()


async def tags_with_counts(
    db: AsyncSession, *, store_owner: int
) -> list[tuple[FinanceTag, int]]:
    """Every live tag with its attachment count, one outer-joined query."""
    rows = (
        await db.exec(
            select(FinanceTag, func.count(FinanceTransactionTag.tag_id))
            .join(
                FinanceTransactionTag,
                FinanceTransactionTag.tag_id == FinanceTag.id,
                isouter=True,
            )
            .where(
                FinanceTag.owner_user_id == store_owner,
                FinanceTag.deleted_at.is_(None),
            )
            .group_by(FinanceTag.id)
            .order_by(FinanceTag.name)
        )
    ).all()
    return [(tag, int(count)) for tag, count in rows]


async def tagged_transaction_ids(
    db: AsyncSession, *, tag_id: int, transaction_ids: list[int]
) -> set[int]:
    """Which of ``transaction_ids`` already wear ``tag_id``."""
    rows = (
        await db.exec(
            select(FinanceTransactionTag.transaction_id).where(
                FinanceTransactionTag.tag_id == tag_id,
                FinanceTransactionTag.transaction_id.in_(transaction_ids),
            )
        )
    ).all()
    return set(rows)


async def tag_links(
    db: AsyncSession, *, tag_id: int, transaction_ids: list[int]
) -> list[FinanceTransactionTag]:
    return list(
        (
            await db.exec(
                select(FinanceTransactionTag).where(
                    FinanceTransactionTag.tag_id == tag_id,
                    FinanceTransactionTag.transaction_id.in_(transaction_ids),
                )
            )
        ).all()
    )


async def tags_by_transaction(
    db: AsyncSession, transaction_ids: list[int] | set[int]
) -> dict[int, list[FinanceTag]]:
    """Tags per transaction, batched for a register page - every id gets
    a key (empty list when untagged), one query for the lot."""
    by_txn: dict[int, list[FinanceTag]] = {txn_id: [] for txn_id in transaction_ids}
    if not by_txn:
        return by_txn
    rows = (
        await db.exec(
            select(FinanceTransactionTag.transaction_id, FinanceTag)
            .join(FinanceTag, FinanceTag.id == FinanceTransactionTag.tag_id)
            .where(
                FinanceTransactionTag.transaction_id.in_(list(by_txn)),
                FinanceTag.deleted_at.is_(None),
            )
            .order_by(FinanceTag.name)
        )
    ).all()
    for txn_id, tag in rows:
        by_txn[txn_id].append(tag)
    return by_txn


async def blank_synced_rows(
    db: AsyncSession,
    *,
    owner_user_id: int | None,
    merchant_ids: Iterable[int] | None = None,
) -> list[FinanceTransaction]:
    """Live rows a feed brought that nobody has filed (#541): no category
    from anyone, not split, not a transfer, not the bank's copy of an
    export's row. ``merchant_ids`` narrows to those payees' rows."""
    txn = FinanceTransaction
    query = select(txn).where(
        txn.connection_id.is_not(None),
        txn.deleted_at.is_(None),
        not_duplicate(),
        txn.category_id.is_(None),
        txn.category_source == "unset",
        txn.is_split.is_(False),
        txn.is_transfer.is_(False),
        *owner_filters(txn.owner_user_id, owner_user_id),
    )
    if merchant_ids is not None:
        query = query.where(txn.merchant_id.in_(list(merchant_ids)))
    return list((await db.exec(query)).all())
