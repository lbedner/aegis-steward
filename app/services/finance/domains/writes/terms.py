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

from collections.abc import Mapping
from datetime import date
from typing import Any, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.components.web_frontend.filters import money_to_cents
from app.core.formatting import format_date
from app.core.schema import known
from app.services.finance.domains.detection.insights.formatting import (
    format_apr,
    format_usd,
)
from app.services.finance.models.accounts import FinanceLiabilityDetail
from app.services.finance.schemas import ChangeDisplayRow

# The fields this change may set, in the order the card lists them:
# what is owed, what it costs, what is due, then where it came from.
# How a lender treats money paid ABOVE the required payment. The answer
# decides whether paying extra shortens the loan or merely pays next
# month early, and it is the difference between a payoff plan that works
# and one that quietly does nothing.
EXTRA_PAYMENT = {
    "principal": "reduce the principal immediately",
    "future_installments": "pay future instalments early, not the principal",
    "unknown": "not confirmed with the lender",
}
PREPAYMENT = {
    "none": "no penalty for paying it off early",
    "penalty": "a penalty applies to early payoff",
    "unknown": "not confirmed with the lender",
}


class Term(NamedTuple):
    """One term of a debt: what it is called, and what KIND of thing it is.

    The kind is the whole point. It is the one fact everything else
    derives from - how the value is parsed out of a form, how it is
    written back in the units the column holds, and how it is read to a
    person. Three places used to know the answer separately: a chain of
    ``if name == "interest_rate_bps"`` in ``shown``, a form template
    listing inputs by hand, and a parser deciding what to do with the
    string. Now they all ask the field.
    """

    name: str
    label: str
    kind: str
    choices: Mapping[str, str] | None = None


FIELDS: tuple[Term, ...] = (
    Term("outstanding_balance", "Balance owed", "money"),
    Term("interest_rate_bps", "Interest rate", "rate"),
    Term("minimum_payment_amount", "Minimum payment", "money"),
    Term("next_payment_due_date", "Next due", "date"),
    Term("origination_date", "Originated", "date"),
    Term("origination_principal", "Original principal", "money"),
    Term("loan_term_months", "Term", "months"),
    Term("liability_type", "Kind", "text"),
    Term("prepayment_penalty", "Early payoff", "choice", PREPAYMENT),
    Term("extra_payment_treatment", "Extra payments", "choice", EXTRA_PAYMENT),
)
BY_NAME: dict[str, Term] = {term.name: term for term in FIELDS}

# Which terms each kind of debt is asked for. A card has no origination
# date and no term in months; a loan has both, and the two questions that
# decide whether paying extra does anything. Selection, not a second
# vocabulary: every name here is a field above.
SHAPES: dict[str, tuple[str, ...]] = {
    "credit_card": (
        "outstanding_balance",
        "interest_rate_bps",
        "minimum_payment_amount",
        "next_payment_due_date",
    ),
    "loan": (
        "outstanding_balance",
        "interest_rate_bps",
        "minimum_payment_amount",
        "next_payment_due_date",
        "origination_principal",
        "origination_date",
        "loan_term_months",
        "prepayment_penalty",
        "extra_payment_treatment",
    ),
}
SHAPES["other_liability"] = SHAPES["credit_card"]


def shape_for(account_type: str) -> tuple[Term, ...]:
    """The terms this kind of account is asked for, in order."""
    return tuple(BY_NAME[name] for name in SHAPES.get(account_type, ()))


# The sources a valuation row may claim, as the model's own constraint
# allows. A price history pasted off a listing site is "zillow"; a
# figure somebody states is "manual".
VALUATION_SOURCES = ("manual", "zillow", "kbb")


class ValuationPoint(BaseModel):
    """One dated figure in a property's history.

    ``note`` is what HAPPENED - "Sold", "Listed for sale", "Price
    change" - because a price history is a series of events and a bare
    number cannot tell a sale from an asking price. ``is_estimate``
    separates a site's guess from a price somebody actually paid, which
    is the difference between equity and hope.
    """

    model_config = ConfigDict(extra="forbid")

    as_of_date: date
    value: int = Field(ge=0)
    note: str | None = None
    is_estimate: bool = False


class ValuationPayload(BaseModel):
    """What an asset was worth, and when - one point or a whole history.

    A list rather than a point per card, because a price history arrives
    as a history: eight rows pasted off a listing site are one thing the
    user is telling you, and eight cards to approve is eight chances to
    approve half of it.
    """

    model_config = ConfigDict(extra="forbid")

    account_id: int
    points: list[ValuationPoint] = Field(min_length=1)
    source: str = "manual"

    _known_source = field_validator("source")(known(VALUATION_SOURCES))

    @model_validator(mode="after")
    def _one_per_date(self) -> ValuationPayload:
        dates = [point.as_of_date for point in self.points]
        if len(set(dates)) != len(dates):
            raise ValueError(
                "Two figures on one date from one source: the later write "
                "would silently replace the earlier. Send one per date."
            )
        return self


async def valuation_execute(
    db: AsyncSession, payload: ValuationPayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.finance.domains.ledger.accounts import get_account
    from app.services.finance.domains.ledger.valuations import upsert_valuation

    account = await get_account(db, payload.account_id, owner_user_id=owner_user_id)
    if account is None:
        raise ValueError(f"Account {payload.account_id} not found.")
    if account.classification != "asset":
        raise ValueError(
            f"{account.name} is a debt; what it is WORTH is a question for "
            "an asset. A debt records what is owed."
        )
    for point in payload.points:
        await upsert_valuation(
            db,
            account_id=payload.account_id,
            as_of_date=point.as_of_date,
            value=point.value,
            owner_user_id=owner_user_id,
            source=payload.source,
            note=point.note,
            is_estimate=point.is_estimate,
        )
    return {"account_id": payload.account_id, "points": len(payload.points)}


async def valuation_describe(
    db: AsyncSession, payload: ValuationPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    from app.services.finance.domains.ledger.accounts import get_account

    account = await get_account(db, payload.account_id, owner_user_id=owner_user_id)
    rows = [
        ChangeDisplayRow(
            label="Asset",
            value=account.name if account else f"account {payload.account_id}",
        )
    ]
    # Oldest first: a history reads forwards, and the shape of it - what
    # it fell to, what it recovered to - is the reason for recording it.
    for point in sorted(payload.points, key=lambda p: p.as_of_date):
        label = format_date(point.as_of_date)
        value = format_usd(point.value)
        if point.note:
            value += f" · {point.note}"
        if point.is_estimate:
            value += " (estimate)"
        rows.append(ChangeDisplayRow(label=label, value=value, amount=point.value))
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
    # Stated, never inferred: "no prepayment penalty" is a claim about
    # somebody's contract, and the only safe default is that nobody has
    # checked. Hence "unknown" as a value you can actually record.
    prepayment_penalty: str | None = None
    extra_payment_treatment: str | None = None

    # None means NOT STATED and has to stay legal: every field here is
    # optional, and a caller sending the whole shape with nulls in the
    # fields it has no answer for is the normal case - a validator that
    # refuses one turns an approvable card into "payload no longer
    # valid" at read time, long after the card was written.
    _known_prepayment_penalty = field_validator("prepayment_penalty")(known(PREPAYMENT))

    _known_extra_payment_treatment = field_validator("extra_payment_treatment")(
        known(EXTRA_PAYMENT)
    )

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
            for name in (term.name for term in FIELDS)
            if getattr(self, name) is not None
        }


def shown(term: Term, value: Any) -> str:
    """One term, in the units a person reads it in."""
    if value is None:
        return "-"
    if term.kind == "rate":
        return format_apr(value)
    if term.kind == "months":
        return f"{value} months"
    if term.kind == "choice" and term.choices:
        return term.choices.get(str(value), str(value))
    if term.kind == "date":
        return format_date(value)
    if term.kind == "money":
        return format_usd(value)
    return str(value)


def typed(term: Term, raw: str) -> tuple[Any, str | None]:
    """A form string in the units the COLUMN holds, or a reason it is not.

    The other half of ``shown``, and deliberately beside it: a rate is
    read as "7.99%" and stored as 799, and the two directions of that one
    fact belong in one place. Blank is not an error - it is "not stated",
    which every term is allowed to be.
    """
    text = (raw or "").strip()
    if not text:
        return None, None
    if term.kind == "money":
        cents = money_to_cents(text)
        return (cents, None) if cents is not None else (None, "is not an amount")
    if term.kind == "rate":
        try:
            return round(float(text.rstrip("%").strip()) * 100), None
        except ValueError:
            return None, "is not a rate"
    if term.kind == "months":
        try:
            return int(text), None
        except ValueError:
            return None, "is not a number of months"
    if term.kind == "date":
        try:
            return date.fromisoformat(text), None
        except ValueError:
            return None, "is not a date"
    if term.kind == "choice" and term.choices and text not in term.choices:
        return None, f"is not one of: {', '.join(term.choices)}"
    return text, None


async def _detail(db: AsyncSession, account_id: int) -> FinanceLiabilityDetail | None:
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
        raise ValueError(f"{account.name} is an asset; only a debt has loan terms.")
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
    for term in FIELDS:
        name = term.name
        if name not in stated:
            continue
        was = getattr(current, name, None) if current else None
        value = shown(term, stated[name])
        # A term that REPLACES one is a different decision from a first
        # term, so the card says which it is - the same rule a memo
        # follows, and for the same reason.
        if was is not None and was != stated[name]:
            value = f"{shown(term, was)} → {value}"
        rows.append(ChangeDisplayRow(label=term.label, value=value))
    return rows
