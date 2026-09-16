"""Creating an account the ledger cannot see, and saying whose it is.

Split out of ``terms`` at the budget, and the seam is a real one: that
module is what a debt COSTS - rates, payments, payoff - and this is what
an account IS. A pension nobody can connect to and a lender with no bank
feed both start here, because until the account exists there is nothing
for a balance, a rate or a document to attach to.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.writes.display import ChangeDisplayRow, format_usd


class CreateAccountPayload(BaseModel):
    """An account the ledger has no other way to learn about.

    A lender with no bank connection is invisible until someone says it
    exists, and until it exists there is nothing for its balance, its
    rate or its payments to attach to - which is a dead end reached
    twice in one conversation: the terms were known, the account was
    not, and the honest answer was that nothing could be filed.

    So this is a proposal like every other: a card naming what would be
    created, approved by the person who knows whether they already have
    it. The balance is what is OWED on a debt, positive cents the way a
    statement says it; the ledger's own sign convention is applied on
    the way in, so a debt cannot be created as an asset by arithmetic.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    account_type: str
    current_balance: int | None = Field(default=None, ge=0)
    institution: str | None = None
    # Whose money, as a party id from the ``parties`` tool. Omitted is
    # ours, which is what every account was before anybody else's was
    # tracked here - and the card says whose, because "add an account"
    # and "add an account that is not ours" are different approvals.
    whose_party_id: int | None = None

    @field_validator("name")
    @classmethod
    def _named(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("An account needs a name.")
        return value.strip()

    @field_validator("account_type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        from app.services.finance.constants import ADD_ACCOUNT_TYPES

        allowed = {key for key, _ in ADD_ACCOUNT_TYPES}
        if value not in allowed:
            raise ValueError(
                f"{value!r} is not an account type. One of: "
                f"{', '.join(sorted(allowed))}."
            )
        return value


async def _similar(db: AsyncSession, name: str, owner_user_id: int | None) -> list[str]:
    """Accounts whose names share a word with this one.

    A duplicate account is the expensive mistake here - transactions
    attach to accounts and balances derive from them - so the card shows
    what the ledger already has that looks like this, and the person
    approving decides. Cheaper than a refusal that is wrong: "GreenSky"
    and "Anthony & Sylvan Pools" are the same debt under two names, and
    no rule can know that.
    """
    from app.services.finance.domains.ledger.accounts import list_accounts
    from app.services.finance.domains.ledger.queries.accounts import EVERYONE

    words = {w for w in name.casefold().split() if len(w) > 3}
    if not words:
        return []
    rows, _ = await list_accounts(
        db,
        owner_user_id=owner_user_id,
        include_hidden=True,
        page_size=500,
        subject_id=EVERYONE,
    )
    return [
        a.name
        for a in rows
        if words & {w for w in a.name.casefold().split() if len(w) > 3}
    ]


async def create_account_execute(
    db: AsyncSession, payload: CreateAccountPayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.finance.constants import account_classification
    from app.services.finance.domains.ledger.accounts import (
        create_manual_account,
        get_or_create_institution,
        list_accounts,
    )
    from app.services.finance.domains.ledger.queries.accounts import EVERYONE

    # EVERYONE, deliberately: the question is whether this account
    # already exists anywhere, and a duplicate check that skips a
    # parent's accounts creates the second copy it exists to prevent.
    existing, _ = await list_accounts(
        db,
        owner_user_id=owner_user_id,
        include_hidden=True,
        page_size=500,
        subject_id=EVERYONE,
    )
    if any(a.name.casefold() == payload.name.casefold() for a in existing):
        raise ValueError(
            f"An account named {payload.name!r} already exists. Record the "
            "terms against it rather than creating a second one."
        )
    classification = account_classification(payload.account_type)
    owed = payload.current_balance or 0
    # The card says "Held with GreenSky", so approval has to deliver it.
    # Find-or-create by normalized name within the owner, the same dedup
    # a payee gets: named twice with different capitals is one row.
    institution = (
        await get_or_create_institution(
            db, name=payload.institution, owner_user_id=owner_user_id
        )
        if payload.institution
        else None
    )
    account = await create_manual_account(
        db,
        name=payload.name,
        account_type=payload.account_type,
        classification=classification,
        owner_user_id=owner_user_id,
        institution_id=institution.id if institution else None,
        # A liability's balance is held negative; the payload states what
        # is OWED, which is how a statement says it.
        current_balance=-owed if classification == "liability" else owed,
    )
    await db.flush()
    whose = await _in_someone_elses_name(
        db, account.id, payload.whose_party_id, owner_user_id
    )
    return {
        "account_id": account.id,
        "name": account.name,
        "classification": classification,
        "institution": institution.name if institution else None,
        "whose": whose,
    }


async def _in_someone_elses_name(
    db: AsyncSession,
    account_id: int,
    party_id: int | None,
    owner_user_id: int | None,
) -> str | None:
    """Put the account in somebody's name, and say whose.

    The subject row is found or made from the party: a person becomes a
    subject the moment an account is put in their name, which is the
    same rule the dialog follows. One person, one identity, whichever
    door the account came through.
    """
    if not party_id:
        return None
    from app.services.finance.domains.ledger.subjects import (
        assign_subject,
        subject_for_party,
    )
    from app.services.matters.service import PartyService

    party = await PartyService(db).get(party_id)
    if party is None:
        raise ValueError(f"Party {party_id} not found.")
    subject = await subject_for_party(
        db, party_id, name=party.name, owner_user_id=owner_user_id
    )
    await assign_subject(db, account_id, subject.id, owner_user_id=owner_user_id)
    return party.name


async def create_account_describe(
    db: AsyncSession, payload: CreateAccountPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    from app.services.finance.constants import account_classification

    classification = account_classification(payload.account_type)
    rows = [
        ChangeDisplayRow(label="New account", value=payload.name),
        ChangeDisplayRow(
            label="Kind", value=f"{payload.account_type} · {classification}"
        ),
    ]
    if payload.current_balance is not None:
        label = "Balance owed" if classification == "liability" else "Balance"
        rows.append(
            ChangeDisplayRow(label=label, value=format_usd(payload.current_balance))
        )
    if payload.institution:
        rows.append(ChangeDisplayRow(label="Held with", value=payload.institution))
    if payload.whose_party_id:
        from app.services.matters.service import PartyService

        party = await PartyService(db).get(payload.whose_party_id)
        rows.append(
            ChangeDisplayRow(
                label="Whose money",
                value=f"{party.name} - kept out of our totals" if party else "Unknown",
            )
        )
    near = await _similar(db, payload.name, owner_user_id)
    if near:
        rows.append(ChangeDisplayRow(label="You already have", value=", ".join(near)))
    return rows
