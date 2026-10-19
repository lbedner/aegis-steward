"""Predicate fragments the ledger's reads share.

Not reads themselves - subqueries and filter clauses several sections
build their WHERE out of. They live apart because "live accounts only"
and "which categories are catch-alls" have to mean one thing whether a
transaction, a category total, or a payee roll-up is asking.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re
from typing import Any

from sqlalchemy import case, func
from sqlmodel import and_, or_, select

from app.services.finance.constants import (
    UNCATEGORIZED_CATEGORY_NAMES,
)
from app.services.finance.models import (
    FinanceAccount,
    FinanceCategory,
    FinanceMerchant,
    FinanceTransaction,
    FinanceTransactionSplit,
    FinanceTransactionTag,
)


def not_duplicate() -> Any:
    """A row that is not another feed's copy of a charge (``two_feeds``)."""
    return FinanceTransaction.dedup_status != "duplicate"


def is_duplicate(row: FinanceTransaction) -> bool:
    """``not_duplicate`` for a row in hand: another feed's copy."""
    return row.dedup_status == "duplicate"


def money_moved_in(account_id: int, owner_user_id: int | None = None) -> list[Any]:
    """The rows that count as money moving in an account: live, and not a
    duplicate of another. What "updated" means for a bank account or a
    card - not when the row was touched, which a rename would do."""
    clauses: list[Any] = [
        FinanceTransaction.account_id == account_id,
        FinanceTransaction.deleted_at.is_(None),
        not_duplicate(),
    ]
    if owner_user_id is not None:
        clauses.append(FinanceTransaction.owner_user_id == owner_user_id)
    return clauses


def live_account_ids():
    """Subquery: ids of accounts that are not soft-deleted.

    NOT the same function as ``detection.insights.rules.live_account_ids``,
    which takes an ``owner_user_id`` and scopes to it. This one is
    owner-blind on purpose - every caller here has already scoped the outer
    query by owner, and re-applying it would be a redundant join. Check
    which one you have imported before assuming a result set is scoped.
    """
    return select(FinanceAccount.id).where(FinanceAccount.deleted_at.is_(None))


def household_account_ids():
    """Subquery: live accounts holding the household's own money - not one
    held here for somebody else (#314), and not one hidden (#315), which
    leaves every total, its history with it. Every income and spending
    figure reads through it, as net worth does; owner-blind like
    ``live_account_ids``. A goal or envelope pot is hidden too, and holds
    no rows: its money moves by valuation."""
    return select(FinanceAccount.id).where(
        FinanceAccount.deleted_at.is_(None),
        FinanceAccount.subject_id.is_(None),
        FinanceAccount.is_hidden.is_(False),
    )


def uncategorized_catchall_ids():
    """Subquery: category ids whose name is a source app's own catch-all
    bucket (see ``UNCATEGORIZED_CATEGORY_NAMES``)."""
    return select(FinanceCategory.id).where(
        func.lower(FinanceCategory.name).in_(list(UNCATEGORIZED_CATEGORY_NAMES))
    )


def categories_filed_as(classification: str) -> Any:
    """Subquery: ids of the categories classified ``classification``."""
    return select(FinanceCategory.id).where(
        FinanceCategory.classification == classification
    )


def money_kind(category: Any, amount: Any) -> Any:
    """What a row counts as in money in against money out (#316), by what
    it is filed under - the one rule every income and spending figure
    reads. ``category`` and ``amount`` are a transaction's columns or a
    split line's, so a split counts line by line.

    ``"transfer"``: money moved - filed as a transfer, or a credit on a
    loan, which is its balance coming down whatever a feed filed it
    under (#318). ``"unfiled"``: a credit nobody filed (no category, or
    a source app's catch-all - "Other Income" is one) - neither income
    nor spending until it is, and said. ``"income"``: filed as income, a
    clawback negative. ``"spending"``: everything else, so a return
    filed under groceries brings groceries down instead of reading as
    income. A split line is judged with its parent's account, which
    every read of lines joins.
    """
    unfiled = or_(category.is_(None), category.in_(uncategorized_catchall_ids()))
    loans = select(FinanceAccount.id).where(FinanceAccount.account_type == "loan")
    return case(
        (and_(amount > 0, FinanceTransaction.account_id.in_(loans)), "transfer"),
        (unfiled, case((amount > 0, "unfiled"), else_="spending")),
        (category.in_(categories_filed_as("income")), "income"),
        (category.in_(categories_filed_as("transfer")), "transfer"),
        else_="spending",
    )


def counts_as(kind: str, *, line: bool = False) -> Any:
    """``money_kind`` is ``kind``, judged on the row - or with ``line`` on
    the split line a split-aware read swaps its parent for."""
    row: Any = FinanceTransactionSplit if line else FinanceTransaction
    return money_kind(row.category_id, row.amount) == kind


def transaction_search_filter(query: str):
    """One OR across every column the register actually shows.

    Searching only ``FinanceTransaction.name`` meant a query matching what
    was ON SCREEN could still return nothing: the Payee column shows the
    assigned merchant (not the descriptor), and the Category and Account
    columns are joins. An empty result then reads as "no such data"
    rather than "that column is not searched".

    A query that reads as money ("500", "1,502.00", "$8.99") ALSO
    matches the amount - exactly, either sign. Exact rather than
    substring, because substring-matching a formatted number finds "50"
    inside "$1,502.00". Date stays out: it has its own affordances
    (column sort, the range chips).
    """
    like = f"%{query}%"
    branches = [
        FinanceTransaction.name.ilike(like),
        FinanceTransaction.merchant_name.ilike(like),
        FinanceTransaction.original_description.ilike(like),
        FinanceTransaction.memo.ilike(like),
        # Subqueries rather than joins: these callers build a filter LIST
        # for an existing select, and a join would mean restructuring both.
        FinanceTransaction.merchant_id.in_(
            select(FinanceMerchant.id).where(FinanceMerchant.name.ilike(like))
        ),
        FinanceTransaction.category_id.in_(
            select(FinanceCategory.id).where(FinanceCategory.name.ilike(like))
        ),
        FinanceTransaction.account_id.in_(
            select(FinanceAccount.id).where(FinanceAccount.name.ilike(like))
        ),
    ]
    cents = _query_cents(query)
    if cents is not None:
        branches.append(func.abs(FinanceTransaction.amount) == cents)
    return or_(*branches)


def _query_cents(query: str) -> int | None:
    """The query as an exact amount in cents, or None if it isn't money.

    Accepts what a person types from a statement: an optional leading
    ``$``, thousands commas, up to two decimals. Decimal arithmetic, so
    "1502.29" is 150229 and never a float neighbour.
    """
    text = query.strip().lstrip("$").replace(",", "")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", text):
        return None
    return int(Decimal(text).scaleb(2))


def split_aware_category_clause(own: Any, line: Any) -> Any:
    """Category membership that respects splits: an unsplit row matches
    by its OWN category clause, a split parent by its LINES' clause -
    the parent's own category stops counting the moment it is split.
    ``own`` predicates ``FinanceTransaction`` columns; ``line``
    predicates ``FinanceTransactionSplit`` ones."""
    return or_(
        and_(FinanceTransaction.is_split.is_(False), own),
        FinanceTransaction.id.in_(
            select(FinanceTransactionSplit.parent_transaction_id).where(line)
        ),
    )


# The axes that take several values, as ``Narrowing`` and the URL name
# them; ``exclude`` holds the ones taken as "all but these".
AXES = ("categories", "payees", "tags", "accounts")


@dataclass(frozen=True)
class Narrowing:
    """The register's finer filters (#326): several values on an axis, or
    with the axis in ``exclude`` everything but them, and an amount range
    by magnitude (a receipt says $8.00, not -800).

    Excluding keeps the rows with no value on the axis - leaving out
    Groceries must not also drop every uncategorized charge, which a bare
    ``NOT IN`` does with its NULLs.
    """

    categories: tuple[int, ...] = ()
    payees: tuple[int, ...] = ()
    tags: tuple[int, ...] = ()
    accounts: tuple[int, ...] = ()
    exclude: frozenset[str] = frozenset()
    amount_min: int | None = None
    amount_max: int | None = None
    reviewed: bool | None = None  # reviewed rows, or not yet (#322)

    def clauses(self) -> list[Any]:
        txn = FinanceTransaction
        picked: list[tuple[str, Any]] = []
        if self.categories:
            own = and_(
                txn.category_id.is_not(None), txn.category_id.in_(self.categories)
            )
            line = FinanceTransactionSplit.category_id.in_(self.categories)
            picked.append(("categories", split_aware_category_clause(own, line)))
        if self.payees:
            picked.append(
                (
                    "payees",
                    and_(
                        txn.merchant_id.is_not(None), txn.merchant_id.in_(self.payees)
                    ),
                )
            )
        if self.tags:
            tagged = select(FinanceTransactionTag.transaction_id).where(
                FinanceTransactionTag.tag_id.in_(self.tags)
            )
            picked.append(("tags", txn.id.in_(tagged)))
        if self.accounts:
            picked.append(("accounts", txn.account_id.in_(self.accounts)))
        clauses = [~c if axis in self.exclude else c for axis, c in picked]
        if self.amount_min is not None:
            clauses.append(func.abs(txn.amount) >= self.amount_min)
        if self.amount_max is not None:
            clauses.append(func.abs(txn.amount) <= self.amount_max)
        if self.reviewed is not None:
            clauses.append(txn.is_reviewed.is_(self.reviewed))
        return clauses
