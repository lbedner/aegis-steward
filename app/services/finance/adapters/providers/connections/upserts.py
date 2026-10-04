"""What every aggregator writes, written once: accounts and transactions.

Each provider maps its own payload onto ``ProviderAccount`` /
``ProviderTransaction`` (its sign convention, its names, its categories)
and hands them here. Finding the row an account or transaction already
is - across a renamed account, a re-linked connection with fresh ids, a
pending charge that posted - is the hard part, and it must not differ by
provider: a third aggregator that re-implemented it would fork history
exactly where the first two learned not to.

Writes but does not commit - the caller owns the transaction.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.time import utcnow
from app.services.finance.adapters.providers import queries
from app.services.finance.models import (
    FinanceAccount,
    FinanceConnection,
    FinanceTransaction,
)
from app.services.finance.service import FinanceService


@dataclass(frozen=True)
class ProviderAccount:
    """One account as a provider reports it, in this app's terms
    (balances in cents, currency lower-case)."""

    provider_account_id: str
    name: str
    mask: str | None
    currency: str
    account_type: str
    classification: str
    current_balance: int | None
    available_balance: int | None = None
    # A provider's id that survives re-linking (Plaid's), when it has one.
    persistent_account_id: str | None = None


@dataclass(frozen=True)
class ProviderTransaction:
    """One transaction as a provider reports it, in this app's terms:
    cents, negative for money out."""

    provider_account_id: str
    external_id: str
    amount: int
    date_: date
    name: str | None
    currency: str
    original_description: str | None = None
    pending: bool = False
    # The provider's id for the pre-auth this posted row replaces.
    pending_provider_id: str | None = None
    # The provider's own category (Plaid's primary), mapped to ours.
    category_primary: str | None = None
    logo_url: str | None = None


def _same_owner(row: FinanceAccount, connection: FinanceConnection) -> bool:
    return (
        connection.owner_user_id is None
        or row.owner_user_id == connection.owner_user_id
    )


def _find_account(
    candidates: list[FinanceAccount],
    connection: FinanceConnection,
    account: ProviderAccount,
) -> FinanceAccount | None:
    """The account this provider account already is, so re-linking the same
    institution (fresh ids) updates the existing row instead of forking it:
    the persistent id, else the provider id (either, deleted or not), else
    a live account of the same owner, name and mask."""
    if account.persistent_account_id:
        for row in candidates:
            if row.persistent_account_id == account.persistent_account_id:
                return row
    for row in candidates:
        if row.provider_account_id == account.provider_account_id:
            return row
    for row in candidates:
        if (
            row.deleted_at is None
            and row.name == account.name
            and row.mask == account.mask
            and _same_owner(row, connection)
        ):
            return row
    return None


def _find_kept(
    candidates: list[FinanceAccount],
    connection: FinanceConnection,
    account: ProviderAccount,
) -> FinanceAccount | None:
    """The one account a disconnect kept at this connection's bank with the
    same kind and mask: a bank can rename an account across a re-link."""
    if not (account.mask and connection.institution_id):
        return None
    kept = [
        row
        for row in candidates
        if row.deleted_at is None
        and row.connection_id is None
        and row.institution_id == connection.institution_id
        and row.mask == account.mask
        and row.account_type == account.account_type
        and _same_owner(row, connection)
    ]
    # Two that fit are not guessed between: the wrong one mixes histories.
    return kept[0] if len(kept) == 1 else None


async def upsert_accounts(
    db: AsyncSession,
    service: FinanceService,
    connection: FinanceConnection,
    provider: str,
    accounts: list[ProviderAccount],
) -> dict[str, int]:
    """Upsert one FinanceAccount per provider account; return
    ``{provider_account_id: account_id}``."""
    accounts = list({a.provider_account_id: a for a in accounts}.values())
    pool = await queries.provider_accounts(
        db,
        provider=provider,
        owner_user_id=connection.owner_user_id,
        ids=[
            i
            for account in accounts
            for i in (account.provider_account_id, account.persistent_account_id)
            if i
        ],
    )
    # Each match leaves the pool: one row is never given two accounts. A
    # kept account is matched last, once ids and names have taken theirs.
    found: list[FinanceAccount | None] = []
    for reported in accounts:
        match = _find_account(pool, connection, reported)
        pool = [row for row in pool if row is not match]
        found.append(match)
    for index, reported in enumerate(accounts):
        if found[index] is None and (match := _find_kept(pool, connection, reported)):
            pool = [row for row in pool if row is not match]
            found[index] = match

    upserted: dict[str, FinanceAccount] = {}
    for reported, account in zip(accounts, found, strict=True):
        await service.get_or_create_currency(reported.currency)
        if account is None:
            account = FinanceAccount(
                owner_user_id=connection.owner_user_id,
                provider=provider,
                account_type=reported.account_type,
                classification=reported.classification,
                name=reported.name,
                is_manual=False,
            )
        # (Re)point at this connection and refresh what the provider says.
        # An institution someone picked for the account outranks the
        # provider's.
        account.connection_id = connection.id
        account.institution_id = account.institution_id or connection.institution_id
        account.provider_account_id = reported.provider_account_id
        account.persistent_account_id = reported.persistent_account_id
        account.currency = reported.currency
        account.name = reported.name
        account.mask = reported.mask
        account.current_balance = reported.current_balance
        account.available_balance = reported.available_balance
        account.balance_as_of = utcnow()
        account.deleted_at = None
        db.add(account)
        upserted[reported.provider_account_id] = account
    await db.flush()
    return {provider_id: account.id for provider_id, account in upserted.items()}


async def apply_transactions(
    db: AsyncSession,
    service: FinanceService,
    transactions: list[ProviderTransaction],
    account_by_provider_id: dict[str, int],
    *,
    connection: FinanceConnection,
    source: str,
    import_batch_id: int | None = None,
) -> tuple[int, int]:
    """Insert new / reconcile existing provider transactions. Returns
    (added, reconciled).

    LANE 1 = ``(account, external_id)``: exact; catches same-connection
    re-syncs. Re-link fallback: a re-linked connection issues fresh ids, so
    a transaction already stored under *another* connection is matched by
    content (account, date, amount, normalized payee) as a multiset.
    Scoping to other connections avoids collapsing legitimate repeat
    charges in a normal sync. The schema forbids a row carrying both id
    and hash, so this is a query-time check, not a stored second lane.
    """
    from app.services.finance.utils import normalize_payee

    prepared = [
        (account_by_provider_id[txn.provider_account_id], txn)
        for txn in transactions
        if txn.provider_account_id in account_by_provider_id
    ]
    if not prepared:
        return 0, 0
    for currency in {txn.currency for _account, txn in prepared}:
        await service.get_or_create_currency(currency)

    touched = {account_id for account_id, _txn in prepared}
    lane1: dict[tuple[int, str], FinanceTransaction] = {}
    other_content: dict[tuple[int, date, int, str], int] = defaultdict(int)
    for row in await queries.provider_rows_for_accounts(
        db, account_ids=touched, source=source
    ):
        if row.external_id is not None:
            lane1[(row.account_id, row.external_id)] = row
        # Content from OTHER connections = a re-linked connection's history.
        if row.connection_id != connection.id:
            other_content[
                (row.account_id, row.date_, row.amount, normalize_payee(row.name or ""))
            ] += 1

    added = reconciled = 0
    # Posted rows naming an earlier pre-auth collapse after the loop, once
    # every row of the batch (a same-batch pending sibling too) is in lane1.
    collapse: list[tuple[int, str, FinanceTransaction]] = []
    for account_id, txn in prepared:
        existing = lane1.get((account_id, txn.external_id))
        if existing is not None:  # same connection re-sync -> update in place
            existing.amount = txn.amount
            existing.name = txn.name
            existing.date_ = txn.date_
            existing.pending = txn.pending
            existing.logo_url = txn.logo_url
            if existing.status != "removed":
                existing.status = "pending" if txn.pending else "posted"
            if txn.pending_provider_id:
                existing.pending_provider_id = txn.pending_provider_id
            # Category precedence: provider < rule < user. A provider refresh
            # never clobbers a rule- or user-assigned category.
            if txn.category_primary and existing.category_source in (
                "provider",
                "unset",
            ):
                existing.category_id = (
                    await service.get_or_create_pfc_category(txn.category_primary)
                ).id
                existing.category_source = "provider"
            db.add(existing)
            reconciled += 1
            if not txn.pending and txn.pending_provider_id:
                collapse.append((account_id, txn.pending_provider_id, existing))
            continue
        content_key = (
            account_id,
            txn.date_,
            txn.amount,
            normalize_payee(txn.name or ""),
        )
        if other_content.get(content_key, 0) > 0:  # re-link: already stored
            other_content[content_key] -= 1
            reconciled += 1
            continue

        category_id = (
            (await service.get_or_create_pfc_category(txn.category_primary)).id
            if txn.category_primary
            else None
        )
        created = await service.create_transaction(
            owner_user_id=connection.owner_user_id,
            account_id=account_id,
            connection_id=connection.id,
            amount=txn.amount,
            txn_date=txn.date_,
            name=txn.name,
            source=source,
            external_id=txn.external_id,
            external_id_source=source,
            currency=txn.currency,
            original_description=txn.original_description,
            category_id=category_id,
            category_source="provider" if txn.category_primary else "unset",
            pending=txn.pending,
            pending_provider_id=txn.pending_provider_id,
            import_batch_id=import_batch_id,
            flush=False,
        )
        created.logo_url = txn.logo_url
        lane1[(account_id, txn.external_id)] = created
        added += 1
        if not txn.pending and txn.pending_provider_id:
            collapse.append((account_id, txn.pending_provider_id, created))

    # Pending -> posted: link the posted row to its pre-auth via the self-FK
    # and tombstone the pre-auth so exactly one row stays visible. The new
    # rows go in together here, and get the ids the link needs.
    await db.flush()
    now = utcnow()
    for account_id, pending_id, posted in collapse:
        pending_row = lane1.get((account_id, pending_id))
        if (
            pending_row is None
            or pending_row is posted
            or pending_row.deleted_at is not None
        ):
            continue
        posted.pending_transaction_id = pending_row.id
        pending_row.deleted_at = now
        db.add(posted)
        db.add(pending_row)
    await db.flush()
    return added, reconciled
