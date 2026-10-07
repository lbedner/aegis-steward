"""Who an account is held with.

Split from ``accounts`` at the budget, and the seam is a real one: an
institution is a body the ledger points at - with the provider ids and
capability flags a connection gates on - and an account is a thing that
holds money. How to REACH a bank (website, phone) is neither: it lives
on the bank's contact, once (#412), and everything here reads and
writes it there.

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


class _Book:
    """This owner's organizations, read once: by normalized name to find
    a bank's contact, by id to fill one in."""

    def __init__(self, parties: list[Any]) -> None:
        self.by_name = {normalize_payee(p.name): p for p in parties}
        self.by_id = {p.id: p for p in parties}


async def _book(db: AsyncSession, owner_user_id: int | None) -> _Book:
    from app.services.matters.service import PartyService

    return _Book(
        await PartyService(db).find(owner_user_id=owner_user_id, kind="organization")
    )


async def _contact_for(db: AsyncSession, inst: FinanceInstitution, book: _Book) -> Any:
    """``inst``'s contact, pointed at and made if the address book lacks
    it: the one already called that, else a new one."""
    from app.services.matters.service import PartyService

    if (party := book.by_id.get(inst.party_id)) is not None:
        return party
    party = book.by_name.get(inst.normalized_name)
    if party is None:
        party = await PartyService(db).create(
            name=inst.name, kind="organization", owner_user_id=inst.owner_user_id
        )
        book.by_name[inst.normalized_name] = book.by_id[party.id] = party
    inst.party_id = party.id
    db.add(inst)
    return party


def _reach(party: Any, *, website: str | None = None, phone: str | None = None) -> None:
    """Fill a contact's blank website or phone; never replace one
    somebody set."""
    contact = dict(party.contact or {})
    for key, value in (("website", website), ("phone", phone)):
        if value and not contact.get(key):
            contact[key] = value
    if contact != (party.contact or {}):
        party.contact = contact


async def websites(
    db: AsyncSession, institution_ids: Iterable[int | None]
) -> dict[int, str | None]:
    """``{institution id: homepage}`` from each bank's contact, one read:
    what its logo and its link are drawn from. Every id asked about is
    answered - ``None`` where there is no website - so a page that read
    them once can tell "none" from "not read" and never reads twice."""
    wanted = [i for i in institution_ids if i is not None]
    contacts = await queries.institution_contacts(db, wanted)
    return {i: contacts.get(i, {}).get("website") or None for i in wanted}


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
    await db.flush()  # an id for the contact to be pointed from
    if inst.party_id is None or url:
        _reach(
            await _contact_for(db, inst, await _book(db, owner_user_id)), website=url
        )
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
    "JPMorgan Chase Bank, N.A.", "Citizens" and "M1 Finance" (#410). The
    homepage it gives fills your bank's contact where it has none. Three
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
        domain: by_id[inst_id]
        for inst_id, site in (await websites(db, [r.id for r in rows])).items()
        if (domain := domain_from_website(site))
    }
    by_name = {inst.normalized_name: inst for inst in rows}
    book = await _book(db, owner_user_id)
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
            await db.flush()
        _reach(await _contact_for(db, inst, book), website=url)
        found[name] = inst
    await db.flush()
    return found


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
    book = await _book(db, owner_user_id)
    for inst in rows:
        await _contact_for(db, inst, book)
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
    from app.services.finance.domains.ledger.merchant_icon import domain_from_website

    rows = await queries.institutions_for_owner(db, owner_user_id=owner_user_id)
    counts = await queries.account_counts_by_institution(
        db, owner_user_id=owner_user_id
    )
    reach = await queries.institution_contacts(db, [inst.id for inst in rows])
    return [
        InstitutionUsage(
            id=inst.id,
            name=inst.name,
            url=(site := reach.get(inst.id, {}).get("website")),
            domain=domain_from_website(site),
            phone=reach.get(inst.id, {}).get("phone"),
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
    """Edit a bank's name, and how to reach it - written on its contact,
    the one home for a website and a phone (#412). A blank clears it: a
    record you can fill and cannot empty keeps every mistake."""
    from app.services.matters.service import PartyService

    inst = await queries.institution_by_id(db, institution_id)
    if inst is None or inst.owner_user_id != owner_user_id:
        return None
    if name is not None and name.strip():
        inst.name = name.strip()
        inst.normalized_name = normalize_payee(inst.name)
    if url is not None or phone is not None:
        party = await _contact_for(db, inst, await _book(db, owner_user_id))
        contact = dict(party.contact or {})
        for key, value in (("website", url), ("phone", phone)):
            if value is not None:
                contact[key] = value.strip()
        await PartyService(db).update(
            party.id, {"contact": {k: v for k, v in contact.items() if v}}
        )
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
