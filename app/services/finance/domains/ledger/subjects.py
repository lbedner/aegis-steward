"""The address book, on the ledger's side.

A subject is a person, trust, or estate whose money this household
tracks but does not own: a parent in care, a child's savings, an estate
being settled. Rows without one are the household's own, which is why
every existing ledger reads unchanged.

The bridge in the other direction lives here too: the institution row
for an organization the app already knows as a party. Both are the same
rule - identity belongs to the address book, and the ledger's row
carries only what a ledger cares about.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.models import FinanceAccount, FinanceSubject
from app.services.finance.utils import utcnow


def subject_filter(value: str | int | None) -> int | None:
    """"ours", "all" or an id, as the listing filter.

    One parser, because the question is asked from a URL, from a tool
    argument and from a form, and three readings of "all" is how one of
    them quietly starts counting a parent's pension as ours. Anything
    unreadable is ours: a typo must not widen a total.
    """
    from app.services.finance.domains.ledger.queries.accounts import (
        EVERYONE,
        HOUSEHOLD,
    )

    if value in (None, "", "ours", "us", "household"):
        return HOUSEHOLD
    if value in ("all", "everyone", "everybody"):
        return EVERYONE
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return HOUSEHOLD


# What a subject can be. The table constrains these too; validating here
# turns a flush-time IntegrityError into an answer the caller can read.
SUBJECT_KINDS = ("person", "trust", "estate", "entity")


async def create_subject(
    db: AsyncSession,
    *,
    name: str,
    kind: str = "person",
    note: str | None = None,
    party_id: int | None = None,
    owner_user_id: int | None = None,
) -> FinanceSubject:
    """Record someone whose money this household tracks."""
    display = (name or "").strip()
    if not display:
        raise ValueError("A subject needs a name.")
    if kind not in SUBJECT_KINDS:
        raise ValueError(
            f"Unknown subject kind {kind!r}; expected one of "
            f"{', '.join(SUBJECT_KINDS)}."
        )
    subject = FinanceSubject(
        owner_user_id=owner_user_id,
        party_id=party_id,
        name=display,
        kind=kind,
        note=note,
    )
    db.add(subject)
    await db.flush()
    return subject


async def list_subjects(
    db: AsyncSession, *, owner_user_id: int | None = None
) -> list[FinanceSubject]:
    query = select(FinanceSubject).where(FinanceSubject.deleted_at.is_(None))
    if owner_user_id is not None:
        query = query.where(FinanceSubject.owner_user_id == owner_user_id)
    return list((await db.exec(query.order_by(FinanceSubject.name))).all())


async def get_subject(
    db: AsyncSession, subject_id: int, *, owner_user_id: int | None = None
) -> FinanceSubject | None:
    query = select(FinanceSubject).where(
        FinanceSubject.id == subject_id, FinanceSubject.deleted_at.is_(None)
    )
    if owner_user_id is not None:
        query = query.where(FinanceSubject.owner_user_id == owner_user_id)
    return (await db.exec(query)).first()


async def assign_subject(
    db: AsyncSession,
    account_id: int,
    subject_id: int | None,
    *,
    owner_user_id: int | None = None,
) -> FinanceAccount | None:
    """Point an account at whose money it holds; None releases it back
    to the household."""
    query = select(FinanceAccount).where(
        FinanceAccount.id == account_id, FinanceAccount.deleted_at.is_(None)
    )
    if owner_user_id is not None:
        query = query.where(FinanceAccount.owner_user_id == owner_user_id)
    account = (await db.exec(query)).first()
    if account is None:
        return None
    if subject_id is not None:
        subject = await get_subject(db, subject_id, owner_user_id=owner_user_id)
        if subject is None:
            raise ValueError(f"Subject {subject_id} not found.")
    account.subject_id = subject_id
    account.updated_at = utcnow()
    db.add(account)
    await db.flush()
    return account


async def subject_for_party(
    db: AsyncSession,
    party_id: int,
    *,
    name: str,
    owner_user_id: int | None = None,
) -> FinanceSubject:
    """The subject for this party, made if it is not there yet.

    Nobody maintains a second list. A person becomes a subject the
    moment an account is put in their name, and the row is found by
    party rather than by name - two people called Bedner are two
    subjects, which is the same call ``party`` itself makes.
    """
    found = (
        await db.exec(
            select(FinanceSubject).where(
                FinanceSubject.party_id == party_id,
                FinanceSubject.deleted_at.is_(None),
            )
        )
    ).first()
    if found is not None:
        return found
    return await create_subject(
        db, name=name, party_id=party_id, owner_user_id=owner_user_id
    )


async def institution_for_party(
    db: AsyncSession,
    party_id: int,
    *,
    name: str,
    website: str | None = None,
    owner_user_id: int | None = None,
) -> Any:
    """The ledger's row for an organization in the address book.

    Found by party rather than by name, the same call ``subject_for_party``
    makes: one body, one identity, whichever door it was named through.
    Naming the NYSLRS on an account otherwise types it a second time, and
    the two drift - a website on one, a logo on the other, and nothing
    saying they are the same body.
    """
    from app.services.finance.domains.ledger.accounts import get_or_create_institution
    from app.services.finance.models import FinanceInstitution

    found = (
        await db.exec(
            select(FinanceInstitution).where(FinanceInstitution.party_id == party_id)
        )
    ).first()
    if found is not None:
        return found
    return await get_or_create_institution(
        db,
        name=name,
        owner_user_id=owner_user_id,
        party_id=party_id,
        **({"url": website} if website else {}),
    )
