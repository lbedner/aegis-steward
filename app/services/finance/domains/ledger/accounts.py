"""Accounts: CRUD, valuations, reconciliation, balance rollups."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.constants import (
    RECONCILE_MARKER,
    Provider,
)
from app.services.finance.domains.ledger import queries, transactions
from app.services.finance.models import (
    FinanceAccount,
    FinanceCurrency,
    FinanceInstitution,
    FinanceLiabilityDetail,
    FinanceTransaction,
)
from app.services.finance.schemas import InstitutionUsage, ReconcileResponse
from app.services.finance.utils import (
    DEFAULT_CURRENCY,
    utcnow,
)


async def get_or_create_currency(
    db: AsyncSession,
    code: str = DEFAULT_CURRENCY,
    *,
    name: str | None = None,
    symbol: str | None = None,
    decimals: int = 2,
) -> FinanceCurrency:
    code = code.lower()
    existing = await queries.currency_by_code(db, code)
    if existing:
        return existing
    currency = FinanceCurrency(
        code=code, name=name or code.upper(), symbol=symbol, decimals=decimals
    )
    db.add(currency)
    await db.flush()
    return currency


async def get_or_create_institution(
    db: AsyncSession,
    *,
    name: str,
    provider: str = "manual",
    provider_institution_id: str | None = None,
    owner_user_id: int | None = None,
    **fields: Any,
) -> FinanceInstitution:
    """The institution row for this name, made if it is new.

    ``provider`` defaults to manual because the common caller now is
    somebody typing the institution an account is held at. A provider
    sync still
    passes its own, and matches on ``provider_institution_id`` first -
    that id is the provider's own identity for the bank and survives a
    rename upstream.

    Otherwise the match is the normalized name within the owner, the
    same dedup a payee gets: typed twice with different capitals is one
    row, and a NULL owner (the provider seeds) is never reached by
    somebody naming their own.
    """
    from app.services.finance.utils import normalize_payee

    if provider_institution_id is not None:
        existing = await queries.institution_by_provider_ref(
            db,
            provider=provider,
            provider_institution_id=provider_institution_id,
        )
        if existing:
            return existing
    normalized = normalize_payee(name)
    if normalized:
        owned = await queries.institution_by_normalized(
            db, normalized=normalized, owner_user_id=owner_user_id
        )
        if owned:
            return owned
    inst = FinanceInstitution(
        owner_user_id=owner_user_id,
        provider=provider,
        name=name.strip(),
        normalized_name=normalized,
        provider_institution_id=provider_institution_id,
        **fields,
    )
    db.add(inst)
    await db.flush()
    return inst


async def list_institutions(
    db: AsyncSession, *, owner_user_id: int | None = None
) -> list[FinanceInstitution]:
    """Who this owner banks with, by name - what the account dialog's
    picker offers before it offers to create one."""
    return await queries.institutions_for_owner(db, owner_user_id=owner_user_id)


async def last_institution_used(
    db: AsyncSession, *, owner_user_id: int | None = None
) -> int | None:
    """The institution to open the picker on: the one most recently set
    on an account. Three brokerage accounts at one bank mean naming it
    once, and the next two arrive already pointing at it."""
    return await queries.latest_account_institution(db, owner_user_id=owner_user_id)


async def institution_usage(
    db: AsyncSession, *, owner_user_id: int | None = None
) -> list[InstitutionUsage]:
    """Every institution with how many accounts sit behind it.

    What the directory is for: a bank nothing uses is safe to delete,
    and two rows for one bank ("Chase" and "Chase Bank") are visible
    here rather than discovered when half the accounts show the wrong
    logo.
    """
    rows = await queries.institutions_for_owner(db, owner_user_id=owner_user_id)
    counts = await queries.account_counts_by_institution(
        db, owner_user_id=owner_user_id
    )
    return [
        InstitutionUsage(
            id=inst.id,
            name=inst.name,
            url=inst.url,
            domain=inst.domain,
            phone=str(inst.metadata_.get("phone") or "") or None,
            account_count=counts.get(inst.id, 0),
        )
        for inst in rows
    ]


async def update_institution(
    db: AsyncSession,
    institution_id: int,
    *,
    owner_user_id: int | None = None,
    name: str | None = None,
    url: str | None = None,
    phone: str | None = None,
) -> FinanceInstitution | None:
    """Edit how to reach a bank.

    ``domain`` is DERIVED from the homepage rather than asked for twice:
    it is only ever the key the icon resolver wants, and a person typing
    their bank's website should not also have to know that.
    """
    from app.services.finance.domains.ledger.merchant_icon import domain_from_website
    from app.services.finance.utils import normalize_payee

    inst = await queries.institution_by_id(db, institution_id)
    if inst is None or inst.owner_user_id != owner_user_id:
        return None
    if name is not None and name.strip():
        inst.name = name.strip()
        inst.normalized_name = normalize_payee(inst.name)
    if url is not None:
        inst.url = url.strip() or None
        inst.domain = domain_from_website(inst.url)
    if phone is not None:
        inst.metadata_ = {**inst.metadata_, "phone": phone.strip()}
    inst.updated_at = utcnow()
    db.add(inst)
    await db.flush()
    return inst


async def set_account_institution(
    db: AsyncSession,
    account_id: int,
    institution_id: int | None,
    *,
    owner_user_id: int | None = None,
) -> FinanceAccount | None:
    """Point an account at the institution it is held at (``None``
    clears it)."""
    account = await get_account(db, account_id, owner_user_id=owner_user_id)
    if account is None:
        return None
    account.institution_id = institution_id
    account.updated_at = utcnow()
    db.add(account)
    await db.flush()
    return account


async def create_manual_account(
    db: AsyncSession,
    *,
    name: str,
    account_type: str,
    classification: str,
    owner_user_id: int | None = None,
    organization_id: int | None = None,
    current_balance: int = 0,
    currency: str = DEFAULT_CURRENCY,
    institution_id: int | None = None,
) -> FinanceAccount:
    await get_or_create_currency(db, currency)
    account = FinanceAccount(
        owner_user_id=owner_user_id,
        organization_id=organization_id,
        provider=Provider.MANUAL,
        name=name,
        account_type=account_type,
        classification=classification,
        current_balance=current_balance,
        currency=currency,
        institution_id=institution_id,
        is_manual=True,
    )
    db.add(account)
    await db.flush()
    return account


async def get_account(
    db: AsyncSession, account_id: int, *, owner_user_id: int | None = None
) -> FinanceAccount | None:
    return await queries.account_by_id(db, account_id, owner_user_id=owner_user_id)


async def list_accounts(
    db: AsyncSession,
    *,
    owner_user_id: int | None = None,
    include_hidden: bool = False,
    page: int = 1,
    page_size: int = 50,
    subject_id: int | None = None,
) -> tuple[list[FinanceAccount], int]:
    return await queries.accounts_page(
        db,
        owner_user_id=owner_user_id,
        include_hidden=include_hidden,
        page=page,
        page_size=page_size,
        subject_id=subject_id,
    )


async def update_account_balance(
    db: AsyncSession,
    account_id: int,
    *,
    current_balance: int,
    owner_user_id: int | None = None,
) -> FinanceAccount | None:
    account = await get_account(db, account_id, owner_user_id=owner_user_id)
    if account is None:
        return None
    account.current_balance = current_balance
    account.balance_as_of = utcnow()
    account.updated_at = utcnow()
    db.add(account)
    await db.flush()
    return account


async def liability_details(
    db: AsyncSession, account_ids: list[int]
) -> dict[int, FinanceLiabilityDetail]:
    """Liability rows for a page of accounts, keyed by account id.

    One ``IN`` query — never one per account. Accounts without a row
    (manual, or AMEX-style institutions that report nothing) are simply
    absent from the map.
    """
    return await queries.liability_details_by_account(db, account_ids)


def effective_balance(
    *,
    current_balance: int | None,
    balance_as_of: object | None,
    classification: str,
    activity_balance: int,
) -> int:
    """The balance an account is actually worth right now.

    Prefer the authoritative ``current_balance`` (provider/statement/
    valuation); for liabilities that figure is the amount owed, so it
    reads negative. Fall back to the transaction-sum activity balance
    when no balance was ever set (e.g. a CSV import with no running
    balance).

    "Never set" is subtle: accounts are CREATED with ``current_balance=0``,
    so a bare zero only counts as a real balance when ``balance_as_of``
    says a balance write actually happened. A nonzero value is trusted
    even unstamped (a hand-entered opening balance has no stamp).
    """
    authoritative = current_balance is not None and (
        current_balance != 0 or balance_as_of is not None
    )
    if authoritative:
        assert current_balance is not None
        if classification == "liability":
            return -abs(current_balance)
        return current_balance
    return activity_balance


async def account_transaction_totals(
    db: AsyncSession,
    *,
    owner_user_id: int | None = None,
    account_ids: list[int] | None = None,
) -> dict[int, int]:
    """Sum of (non-duplicate, non-deleted) transaction amounts per account.

    The register-style balance shown per account in the UI when no
    statement balance/valuation is set. One aggregate query, keyed by
    account id — never one query per account. Pass ``account_ids`` to scope
    the aggregate to a page's accounts instead of the whole owner.
    """
    return await queries.transaction_totals_by_account(
        db, owner_user_id=owner_user_id, account_ids=account_ids
    )


async def update_account(
    db: AsyncSession,
    account_id: int,
    *,
    owner_user_id: int | None = None,
    name: str | None = None,
    is_hidden: bool | None = None,
    is_closed: bool | None = None,
) -> FinanceAccount | None:
    """Rename / hide / close an account. Returns None if not found/owned."""
    account = await get_account(db, account_id, owner_user_id=owner_user_id)
    if account is None:
        return None
    if name is not None:
        account.name = name
    if is_hidden is not None:
        account.is_hidden = is_hidden
    if is_closed is not None:
        account.is_closed = is_closed
    account.updated_at = utcnow()
    db.add(account)
    await db.flush()
    return account


async def soft_delete_account(
    db: AsyncSession, account_id: int, *, owner_user_id: int | None = None
) -> bool:
    """Soft-delete (set ``deleted_at``); never hard-delete. False if absent."""
    account = await get_account(db, account_id, owner_user_id=owner_user_id)
    if account is None:
        return False
    account.deleted_at = utcnow()
    db.add(account)
    await db.flush()
    return True


async def register_balance_as_of(db: AsyncSession, account_id: int, as_of: date) -> int:
    """Signed sum of the account's posted register through ``as_of``."""
    return await queries.register_balance_through(db, account_id, as_of)


async def reconcile_adjustment_for(
    db: AsyncSession, account_id: int, statement_date: date
) -> FinanceTransaction | None:
    return await queries.reconcile_adjustment_on(db, account_id, statement_date)


async def reconcile_preview(
    db: AsyncSession,
    account_id: int,
    *,
    owner_user_id: int | None = None,
    statement_date: date,
    statement_balance: int,
) -> ReconcileResponse | None:
    """What reconciling WOULD do: the register-vs-statement delta.

    The register figure excludes any prior adjustment for this same
    statement date - re-reconciling replaces it, so the delta must be
    measured as if it were not there.
    """
    account = await get_account(db, account_id, owner_user_id=owner_user_id)
    if account is None:
        return None
    has_register = await queries.has_nonreconcile_register(db, account_id)
    register = await register_balance_as_of(db, account_id, statement_date)
    existing = await reconcile_adjustment_for(db, account_id, statement_date)
    if existing is not None:
        register -= existing.amount
    return ReconcileResponse(
        account_id=account_id,
        route="adjustment" if has_register else "valuation",
        statement_date=statement_date,
        statement_balance=statement_balance,
        register_balance=register,
        delta=statement_balance - register,
        adjustment_transaction_id=existing.id if existing else None,
        applied=False,
    )


async def reconcile_account(
    db: AsyncSession,
    account_id: int,
    *,
    owner_user_id: int | None = None,
    statement_date: date,
    statement_balance: int,
) -> ReconcileResponse | None:
    """Reconcile the account to a statement. Idempotent per date.

    Adjustment route: one transfer-flagged transaction dated the
    statement day absorbs the delta (replaced on re-reconcile; removed
    when the delta reaches zero). Valuation route (no register): the
    statement balance is posted as a valuation. Either way the
    account's waterline (``metadata.reconciled_through``) and headline
    balance move, and net-worth snapshots recompute from the statement
    date FORWARD - history before it is untouched.
    """
    preview = await reconcile_preview(
        db,
        account_id,
        owner_user_id=owner_user_id,
        statement_date=statement_date,
        statement_balance=statement_balance,
    )
    if preview is None:
        return None
    account = await get_account(db, account_id, owner_user_id=owner_user_id)
    delta = preview.delta
    result = preview.model_copy(update={"applied": True})

    if preview.route == "valuation":
        # Imported here, not at module scope: ``valuations`` reads accounts
        # (get_account), so a top-level import back would be circular.
        from app.services.finance.domains.ledger.valuations import upsert_valuation

        await upsert_valuation(
            db,
            account_id=account_id,
            as_of_date=statement_date,
            value=statement_balance,
            owner_user_id=owner_user_id,
            note="Reconciled to statement",
        )
        result.adjustment_transaction_id = None
    else:
        existing = await reconcile_adjustment_for(db, account_id, statement_date)
        audit = (
            f"Reconciled to statement: {statement_balance / 100:,.2f} "
            f"(register showed {preview.register_balance / 100:,.2f})"
        )
        if delta == 0:
            if existing is not None:
                await db.delete(existing)
                await db.flush()
            result.adjustment_transaction_id = None
        elif existing is not None:
            existing.amount = delta
            existing.memo = audit
            existing.updated_at = utcnow()
            db.add(existing)
            await db.flush()
            result.adjustment_transaction_id = existing.id
        else:
            txn = await transactions.create_transaction(
                db,
                account_id=account_id,
                amount=delta,
                txn_date=statement_date,
                owner_user_id=owner_user_id,
                name="Balance adjustment",
                external_id=f"reconcile:{statement_date.isoformat()}",
                external_id_source=RECONCILE_MARKER,
                memo=audit,
            )
            # Balance-space, not spend-space: every analytics consumer
            # filters transfers out; the balance walks keep them.
            txn.is_transfer = True
            db.add(txn)
            await db.flush()
            result.adjustment_transaction_id = txn.id
        # The statement is the freshest balance fact we hold unless a
        # later one is already stamped.
        if account.balance_as_of is None or (
            statement_date >= account.balance_as_of.date()
        ):
            account.current_balance = statement_balance
            account.balance_as_of = datetime(
                statement_date.year, statement_date.month, statement_date.day
            )

    account.metadata_ = {
        **(account.metadata_ or {}),
        "reconciled_through": statement_date.isoformat(),
    }
    db.add(account)
    await db.flush()
    result.reconciled_through = statement_date

    from app.services.finance.domains.ledger import networth

    await networth.recompute_snapshots(
        db, owner_user_id=owner_user_id, start_date=statement_date
    )
    return result
