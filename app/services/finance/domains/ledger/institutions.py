"""Who an account is held with.

Split from ``accounts`` at the budget, and the seam is a real one: an
institution is a body the ledger points at - with a logo, a domain and
the capability flags a connection gates on - and an account is a thing
that holds money. They are read together and changed apart.

``institution_for_party`` lives in ``subjects``, with the other bridge
into the address book.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger import queries
from app.services.finance.models import FinanceAccount, FinanceInstitution
from app.services.finance.schemas import InstitutionUsage
from app.services.finance.utils import normalize_payee, utcnow

# The contact is where everything about a bank that is not about one
# account lives - website, phone, address, the letters it sends - and
# the reader files paper by what the contact holds (#410). So a bank is
# never made without one: the one already called that, else a new one.


async def _contacts(db: AsyncSession, owner_user_id: int | None) -> dict[str, Any]:
    """This owner's organizations, by normalized name: one read."""
    from app.services.matters.service import PartyService

    found = await PartyService(db).find(
        owner_user_id=owner_user_id, kind="organization"
    )
    return {normalize_payee(party.name): party for party in found}


async def _contact_for(
    db: AsyncSession, inst: FinanceInstitution, contacts: dict[str, Any]
) -> None:
    """Point ``inst`` at its contact, made if the address book lacks it."""
    from app.services.matters.service import PartyService

    if inst.party_id is not None:
        return
    party = contacts.get(inst.normalized_name)
    if party is None:
        party = await PartyService(db).create(
            name=inst.name,
            kind="organization",
            owner_user_id=inst.owner_user_id,
            contact={"website": inst.url} if inst.url else None,
        )
        contacts[inst.normalized_name] = party
    inst.party_id = party.id
    db.add(inst)


def _new(
    db: AsyncSession, name: str, owner_user_id: int | None, **fields: Any
) -> FinanceInstitution:
    """A bank's row, keyed by its normalized name within the owner."""
    inst = FinanceInstitution(
        owner_user_id=owner_user_id,
        name=name.strip(),
        normalized_name=normalize_payee(name),
        **fields,
    )
    db.add(inst)
    return inst


def _homepage(inst: FinanceInstitution, url: str | None) -> None:
    """A homepage fills a blank, never replaces one somebody set; the
    domain is derived from it, the key the icon resolver wants."""
    from app.services.finance.domains.ledger.merchant_icon import domain_from_website

    if url and not inst.url:
        inst.url = url
    if inst.url and not inst.domain:
        inst.domain = domain_from_website(inst.url)


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
    inst = (
        await queries.institution_by_normalized(
            db, normalized=normalized, owner_user_id=owner_user_id
        )
        if normalized
        else None
    )
    url = fields.pop("url", None)
    if inst is None:
        inst = _new(
            db,
            name,
            owner_user_id,
            provider=provider,
            provider_institution_id=provider_institution_id,
            **fields,
        )
    _homepage(inst, url)
    if inst.party_id is None:
        await _contact_for(db, inst, await _contacts(db, owner_user_id))
    await db.flush()
    return inst


async def institutions_for_banks(
    db: AsyncSession,
    reported: Iterable[tuple[Any, FinanceAccount | None]],
    *,
    owner_user_id: int | None = None,
) -> dict[str, FinanceInstitution]:
    """``{bank name: institution}`` for the banks a link reports: each
    reported account (its ``bank``, ``bank_url``) with the account of
    yours it already is, if any.

    A bank is yours under whatever name you gave it: the one its
    accounts already sit under, else the one with its website, else the
    one called that - only then a new row and contact. Live, SimpleFIN's
    "Chase Bank", "Citizens Bank" and "M1" each made a second row beside
    "JPMorgan Chase Bank, N.A.", "Citizens" and "M1 Finance" (#410). What
    it says of itself fills your row's blanks and its contact's. Two
    reads for the batch, however many banks."""
    from app.services.finance.domains.ledger.merchant_icon import domain_from_website

    banks = [
        (said.bank, said.bank_url, account.institution_id if account else None)
        for said, account in reported
        if said.bank
    ]
    if not banks:
        return {}
    rows = await queries.institutions_for_owner(db, owner_user_id=owner_user_id)
    by_id = {inst.id: inst for inst in rows}
    by_domain = {
        domain: inst
        for inst in rows
        if (domain := inst.domain or domain_from_website(inst.url))
    }
    by_name = {inst.normalized_name: inst for inst in rows}
    contacts = await _contacts(db, owner_user_id)
    found: dict[str, FinanceInstitution] = {}
    for name, url, under in banks:
        inst = (
            found.get(name)
            or by_id.get(under)
            or by_domain.get(domain_from_website(url) or "")
            or by_name.get(normalize_payee(name))
        )
        if inst is None:
            inst = _new(db, name, owner_user_id, provider="manual")
            by_name[inst.normalized_name] = inst
        _homepage(inst, url)
        await _contact_for(db, inst, contacts)
        _website_on_contact(inst, contacts)
        found[name] = inst
    await db.flush()
    return found


def _website_on_contact(inst: FinanceInstitution, contacts: dict[str, Any]) -> None:
    """The bank's homepage on its contact, where the contact has none:
    the contact is what the reader files a bank's paper by."""
    party = next((p for p in contacts.values() if p.id == inst.party_id), None)
    if party is None or not inst.url or (party.contact or {}).get("website"):
        return
    party.contact = {**(party.contact or {}), "website": inst.url}


async def ensure_contacts(db: AsyncSession, *, owner_user_id: int | None = None) -> int:
    """The one-time pass (#410): a contact for every institution made
    before a bank came with one. Returns how many were made or matched;
    a second run returns 0."""
    rows = [
        inst
        for inst in await queries.institutions_for_owner(
            db, owner_user_id=owner_user_id
        )
        if inst.party_id is None
    ]
    if not rows:
        return 0
    contacts = await _contacts(db, owner_user_id)
    for inst in rows:
        await _contact_for(db, inst, contacts)
    await db.flush()
    return len(rows)


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
