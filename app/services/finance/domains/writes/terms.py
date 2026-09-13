"""An account the ledger cannot see, and what a loan COSTS.

Both stated by the person who has the statement in front of them.

``FinanceLiabilityDetail`` has held a loan's terms all along - balance,
rate, minimum payment, due date, origination, term - and the app reads
them everywhere: the accounts page draws "Due Jul 15 · min $35.00" from
them, and ``accounts()`` hands the whole block to the assistant. Nothing
ever WROTE them but a Plaid sync, so a loan from a lender with no bank
connection could be created, could hold a balance, and could never be
told its own rate.

That is most consumer financing - a pool loan, a dealer note, a
0%-promo store card - and without a rate nothing can answer "what is
this costing me", payoff ordering cannot be asked at all, and the
monthly payment reads as ordinary spending. Live: two $224 payments sat
under Home:Pool, counted as pool operating cost.

Every field is OPTIONAL except which account, because these arrive a
few at a time - a statement gives the balance and the rate, the portal
adds the due date later - and a card that demanded all of them would be
a card nobody could approve. What is sent is what is set; what is
omitted is left exactly as it was.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.formatting import format_date
from app.services.finance.domains.detection.insights.formatting import (
    format_apr,
    format_usd,
)
from app.services.finance.models.accounts import FinanceLiabilityDetail
from app.services.finance.schemas import ChangeDisplayRow

# The fields this change may set, in the order the card lists them:
# what is owed, what it costs, what is due, then where it came from.
FIELDS: tuple[tuple[str, str], ...] = (
    ("outstanding_balance", "Balance owed"),
    ("interest_rate_bps", "Interest rate"),
    ("minimum_payment_amount", "Minimum payment"),
    ("next_payment_due_date", "Next due"),
    ("origination_date", "Originated"),
    ("origination_principal", "Original principal"),
    ("loan_term_months", "Term"),
    ("liability_type", "Kind"),
)


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


async def _similar(
    db: AsyncSession, name: str, owner_user_id: int | None
) -> list[str]:
    """Accounts whose names share a word with this one.

    A duplicate account is the expensive mistake here - transactions
    attach to accounts and balances derive from them - so the card shows
    what the ledger already has that looks like this, and the person
    approving decides. Cheaper than a refusal that is wrong: "GreenSky"
    and "Anthony & Sylvan Pools" are the same debt under two names, and
    no rule can know that.
    """
    from app.services.finance.domains.ledger.accounts import list_accounts

    words = {w for w in name.casefold().split() if len(w) > 3}
    if not words:
        return []
    rows, _ = await list_accounts(
        db, owner_user_id=owner_user_id, include_hidden=True, page_size=500
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

    existing, _ = await list_accounts(
        db, owner_user_id=owner_user_id, include_hidden=True, page_size=500
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
    return {
        "account_id": account.id,
        "name": account.name,
        "classification": classification,
        "institution": institution.name if institution else None,
    }


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
    near = await _similar(db, payload.name, owner_user_id)
    if near:
        rows.append(
            ChangeDisplayRow(label="You already have", value=", ".join(near))
        )
    return rows


class LoanTermsPayload(BaseModel):
    """The terms of one loan, as the user states them.

    Rates are BASIS POINTS, the unit the column already holds: 7.99%
    is 799, and a percentage sent as a float would round into a rate
    nobody agreed to. Money is positive cents - what is OWED, not a
    signed ledger amount, because that is how a statement says it.
    """

    model_config = ConfigDict(extra="forbid")

    account_id: int
    outstanding_balance: int | None = Field(default=None, ge=0)
    interest_rate_bps: int | None = Field(default=None, ge=0, le=1_000_000)
    minimum_payment_amount: int | None = Field(default=None, ge=0)
    next_payment_due_date: date | None = None
    origination_date: date | None = None
    origination_principal: int | None = Field(default=None, ge=0)
    loan_term_months: int | None = Field(default=None, ge=1)
    liability_type: str | None = None

    @model_validator(mode="after")
    def _something_to_set(self) -> LoanTermsPayload:
        if not self.stated:
            raise ValueError(
                "State at least one term; an empty change is not a change."
            )
        return self

    @property
    def stated(self) -> dict[str, Any]:
        """Only the fields this payload actually names."""
        return {
            name: getattr(self, name)
            for name, _ in FIELDS
            if getattr(self, name) is not None
        }


def shown(name: str, value: Any) -> str:
    """One term, in the units a person reads it in."""
    if value is None:
        return "-"
    if name == "interest_rate_bps":
        return format_apr(value)
    if name == "loan_term_months":
        return f"{value} months"
    if name.endswith("_date"):
        return format_date(value)
    if isinstance(value, int):
        return format_usd(value)
    return str(value)


async def _detail(
    db: AsyncSession, account_id: int
) -> FinanceLiabilityDetail | None:
    return (
        await db.exec(
            select(FinanceLiabilityDetail).where(
                FinanceLiabilityDetail.account_id == account_id
            )
        )
    ).first()


async def loan_terms_execute(
    db: AsyncSession, payload: LoanTermsPayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.finance.domains.ledger.accounts import get_account
    from app.services.finance.utils import utcnow

    account = await get_account(db, payload.account_id, owner_user_id=owner_user_id)
    if account is None:
        raise ValueError(f"Account {payload.account_id} not found.")
    if account.classification != "liability":
        raise ValueError(
            f"{account.name} is an asset; only a debt has loan terms."
        )
    detail = await _detail(db, payload.account_id)
    if detail is None:
        detail = FinanceLiabilityDetail(
            owner_user_id=owner_user_id, account_id=payload.account_id
        )
    for name, value in payload.stated.items():
        setattr(detail, name, value)
    detail.updated_at = utcnow()
    db.add(detail)
    await db.flush()
    return {"account_id": payload.account_id, "set": sorted(payload.stated)}


async def loan_terms_describe(
    db: AsyncSession, payload: LoanTermsPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    from app.services.finance.domains.ledger.accounts import get_account

    account = await get_account(db, payload.account_id, owner_user_id=owner_user_id)
    rows = [
        ChangeDisplayRow(
            label="Account",
            value=account.name if account else f"Account {payload.account_id}",
        )
    ]
    current = await _detail(db, payload.account_id)
    stated = payload.stated
    for name, label in FIELDS:
        if name not in stated:
            continue
        was = getattr(current, name, None) if current else None
        value = shown(name, stated[name])
        # A term that REPLACES one is a different decision from a first
        # term, so the card says which it is - the same rule a memo
        # follows, and for the same reason.
        if was is not None and was != stated[name]:
            value = f"{shown(name, was)} → {value}"
        rows.append(ChangeDisplayRow(label=label, value=value))
    return rows
