"""Who an account is held with.

Split from ``accounts`` at the budget, and the seam is a real one: an
institution is a body the ledger points at - with a logo, a domain and
the capability flags a connection gates on - and an account is a thing
that holds money. They are read together and changed apart.

``institution_for_party`` lives in ``subjects``, with the other bridge
into the address book.
"""

from __future__ import annotations

from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger import queries
from app.services.finance.models import FinanceAccount, FinanceInstitution
from app.services.finance.schemas import InstitutionUsage
from app.services.finance.utils import utcnow


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
    account = await queries.account_by_id(db, account_id, owner_user_id=owner_user_id)
    if account is None:
        return None
    account.institution_id = institution_id
    account.updated_at = utcnow()
    db.add(account)
    await db.flush()
    return account
