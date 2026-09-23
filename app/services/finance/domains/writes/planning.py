"""The plan's writes as cards: goals and envelopes.

Bills and income already went through the queue; goals and envelopes
did not, so the assistant could see a wrong goal and not fix it (#166).
None of these moves money. They change what the plan assumes, so each
card says what the forecast will do about it, not only which field
changed: "sets aside $250.00 on the 1st of each month".

Every card runs the function the Budget page already runs, so a goal
changed by a card and one changed by hand are changed the same way.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.schema import known
from app.services.finance.domains.detection.insights.formatting import format_usd
from app.services.finance.domains.planning.envelopes import (
    ENVELOPE_CADENCES,
    envelope_metadata,
)
from app.services.finance.domains.planning.goals import DEFAULT_PRIORITY, goal_metadata
from app.services.finance.schemas import ChangeDisplayRow

GoalStatus = Literal["active", "paused", "reached"]
# How an envelope's cadence reads as a rate period (``matters.facts``).
_PERIOD = {"weekly": "week", "monthly": "month"}


async def _forecast(
    db: AsyncSession, meta: Any, *, account_id: int | None, balance: int
) -> str | None:
    """What the forecast will set aside for this goal each month, if the
    card is approved, in words.

    Asked of the allocation engine, the one every consumer reads - the
    forecast, the Budget page, auto-contribute - with this goal changed
    as the card proposes and every other goal as it stands, so priority
    and surplus come out the way they will. The card used to do its own
    sum and said "$100.00" while the page said "$300.00/mo": with a
    target date the engine asks what the date needs, and the declared
    amount is not used (2026-09-23).
    """
    from types import SimpleNamespace

    from app.services.finance.domains.planning import allocation, goals
    from app.services.finance.utils import current_date

    if meta.status != "active":
        return f"sets nothing aside while it is {meta.status}"
    today = current_date()
    rows = [
        SimpleNamespace(
            id=one.id, metadata_=one.metadata_, current_balance=one.current_balance
        )
        for one in await goals.list_goals(db, owner_user_id=None)
        if one.id != account_id
    ]
    rows.append(
        SimpleNamespace(
            id=account_id or 0,
            metadata_=goals.set_goal_metadata({}, **_stored(meta)),
            current_balance=balance,
        )
    )
    figures = await allocation.month_figures(db, owner_user_id=None, today=today)
    ask = allocation.asks_by_account(rows, figures, today=today).get(account_id or 0, 0)
    said = f"sets aside {format_usd(ask)} on the 1st of each month"
    if (
        meta.target_date
        and meta.monthly_contribution
        and meta.contribution_kind == "fixed"
        and ask != meta.monthly_contribution
    ):
        said += (
            f" - what reaching {format_usd(meta.target_amount)} by "
            f"{meta.target_date.isoformat()} needs; "
            f"{format_usd(meta.monthly_contribution)} a month is not used while it has a date"
        )
    return said


def _stored(meta: Any) -> dict[str, Any]:
    """A ``GoalMeta`` as ``set_goal_metadata``'s keyword arguments."""
    return {
        "target_amount": meta.target_amount,
        "target_date": meta.target_date,
        "monthly_contribution": meta.monthly_contribution,
        "status": meta.status,
        "contribution_kind": meta.contribution_kind,
        "contribution_bps": meta.contribution_bps,
        "priority": meta.priority,
        "target_rule": meta.target_rule,
        "target_factor": meta.target_factor,
        "target_scope": meta.target_scope,
    }


def _allowance(cents: int | None, cadence: str) -> str:
    from app.services.matters.facts import monthly_cents

    if cents is None:
        return "-"
    said = f"{format_usd(cents)} a {_PERIOD[cadence]}"
    monthly = monthly_cents(cents, _PERIOD[cadence])
    if cadence != "monthly" and monthly is not None:
        said += f" (about {format_usd(monthly)} a month)"
    return said


async def _account(db: AsyncSession, account_id: int) -> Any:
    from app.services.finance.domains.ledger.accounts import get_account

    account = await get_account(db, account_id, owner_user_id=None)
    if account is None:
        raise ValueError(f"Account {account_id} not found.")
    return account


class GoalCreatePayload(BaseModel):
    """A new goal of its own (a hidden account holding what is set aside)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    target_cents: int = Field(gt=0)
    target_date: date | None = None
    monthly_cents: int | None = Field(default=None, ge=0)
    priority: int = DEFAULT_PRIORITY


async def goal_create_execute(
    db: AsyncSession, payload: GoalCreatePayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.finance.service import FinanceService

    account = await FinanceService(db).create_virtual_goal(
        owner_user_id=owner_user_id,
        name=payload.name.strip(),
        target_amount=payload.target_cents,
        target_date=payload.target_date,
        monthly_contribution=payload.monthly_cents,
        priority=payload.priority,
    )
    return {"account_id": account.id, "name": account.name}


async def goal_create_describe(
    db: AsyncSession, payload: GoalCreatePayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    from app.services.finance.domains.planning.goals import GoalMeta

    target = format_usd(payload.target_cents)
    if payload.target_date:
        target += f" by {payload.target_date.isoformat()}"
    rows = [
        ChangeDisplayRow(label="Goal", value=payload.name.strip()),
        ChangeDisplayRow(label="Target", value=target),
    ]
    said = await _forecast(
        db,
        GoalMeta(
            target_amount=payload.target_cents,
            target_date=payload.target_date,
            monthly_contribution=payload.monthly_cents,
            priority=payload.priority,
        ),
        account_id=None,
        balance=0,
    )
    if said:
        rows.append(ChangeDisplayRow(label="Forecast", value=said))
    return rows


class GoalUpdatePayload(BaseModel):
    """A goal changed: only what is given changes."""

    model_config = ConfigDict(extra="forbid")

    account_id: int
    target_cents: int | None = Field(default=None, gt=0)
    target_date: date | None = None
    monthly_cents: int | None = Field(default=None, ge=0)
    priority: int | None = None
    status: GoalStatus | None = None

    @model_validator(mode="after")
    def _says_something(self) -> GoalUpdatePayload:
        if not self.changes():
            raise ValueError("Nothing to change: give at least one field.")
        return self

    def changes(self) -> dict[str, Any]:
        """In ``goal_update.update_goal``'s field names."""
        named = {
            "target_amount": self.target_cents,
            "target_date": self.target_date,
            "monthly_contribution": self.monthly_cents,
            "priority": self.priority,
            "status": self.status,
        }
        return {name: value for name, value in named.items() if value is not None}


# Field, card label, and how its value reads.
_GOAL_FIELDS: tuple[tuple[str, str, Any], ...] = (
    ("target_amount", "Target", format_usd),
    ("target_date", "By", lambda d: d.isoformat()),
    ("monthly_contribution", "Each month", format_usd),
    ("priority", "Priority", str),
    ("status", "Status", str),
)


async def _goal(db: AsyncSession, account_id: int) -> tuple[Any, Any]:
    account = await _account(db, account_id)
    meta = goal_metadata(account.metadata_)
    if meta is None:
        raise ValueError(f"{account.name} is not a goal.")
    return account, meta


async def goal_update_execute(
    db: AsyncSession, payload: GoalUpdatePayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.finance.domains.planning.goal_update import update_goal

    await _goal(db, payload.account_id)
    await update_goal(
        db, payload.account_id, payload.changes(), owner_user_id=owner_user_id
    )
    return {"account_id": payload.account_id, "changed": sorted(payload.changes())}


async def goal_update_describe(
    db: AsyncSession, payload: GoalUpdatePayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    account, meta = await _goal(db, payload.account_id)
    changes = payload.changes()
    rows = [ChangeDisplayRow(label="Goal", value=account.name)]
    for name, label, said in _GOAL_FIELDS:
        if name in changes and changes[name] != getattr(meta, name):
            standing = getattr(meta, name)
            was = said(standing) if standing is not None else "-"
            rows.append(
                ChangeDisplayRow(label=label, value=f"{was} → {said(changes[name])}")
            )
    if len(rows) == 1:
        raise ValueError(f"This would change nothing about {account.name}.")
    balance = account.current_balance or 0
    after = meta.model_copy(update=changes)
    said_after = await _forecast(db, after, account_id=account.id, balance=balance)
    if said_after and said_after != await _forecast(
        db, meta, account_id=account.id, balance=balance
    ):
        rows.append(ChangeDisplayRow(label="Forecast", value=said_after))
    return rows


# What an envelope card always says, because it is the mistake to avoid:
# a weekly allowance read as a weekly bank transfer.
_NO_MONEY = "moves none: it is what the plan allows, not a transfer"


class EnvelopeCreatePayload(BaseModel):
    """A new envelope: an allowance somebody spends down."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    allowance_cents: int = Field(ge=0)
    cadence: str = "monthly"
    _known_cadence = field_validator("cadence")(known(ENVELOPE_CADENCES))


async def envelope_create_execute(
    db: AsyncSession, payload: EnvelopeCreatePayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.finance.service import FinanceService

    account = await FinanceService(db).create_envelope(
        owner_user_id=owner_user_id,
        name=payload.name.strip(),
        monthly_credit=payload.allowance_cents,
        cadence=payload.cadence,
    )
    return {"account_id": account.id, "name": account.name}


async def envelope_create_describe(
    db: AsyncSession, payload: EnvelopeCreatePayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    return [
        ChangeDisplayRow(label="Envelope", value=payload.name.strip()),
        ChangeDisplayRow(
            label="Allowance",
            value=_allowance(payload.allowance_cents, payload.cadence),
        ),
        ChangeDisplayRow(label="Money", value=_NO_MONEY),
    ]


class EnvelopeUpdatePayload(BaseModel):
    """An envelope changed: only what is given changes."""

    model_config = ConfigDict(extra="forbid")

    account_id: int
    allowance_cents: int | None = Field(default=None, ge=0)
    cadence: str | None = None
    auto_credit: bool | None = None
    _known_cadence = field_validator("cadence")(known(ENVELOPE_CADENCES))

    @model_validator(mode="after")
    def _says_something(self) -> EnvelopeUpdatePayload:
        if (
            self.allowance_cents is None
            and self.cadence is None
            and self.auto_credit is None
        ):
            raise ValueError("Nothing to change: give at least one field.")
        return self


async def _envelope(db: AsyncSession, account_id: int) -> tuple[Any, Any]:
    account = await _account(db, account_id)
    meta = envelope_metadata(account.metadata_)
    if meta is None:
        raise ValueError(f"{account.name} is not an envelope.")
    return account, meta


def _merged(meta: Any, payload: EnvelopeUpdatePayload) -> tuple[int | None, str, bool]:
    return (
        payload.allowance_cents
        if payload.allowance_cents is not None
        else meta.monthly_credit,
        payload.cadence or meta.cadence,
        payload.auto_credit if payload.auto_credit is not None else meta.auto_credit,
    )


async def envelope_update_execute(
    db: AsyncSession, payload: EnvelopeUpdatePayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.finance.service import FinanceService

    _account_row, meta = await _envelope(db, payload.account_id)
    allowance, cadence, auto = _merged(meta, payload)
    await FinanceService(db).update_envelope(
        payload.account_id,
        owner_user_id=owner_user_id,
        monthly_credit=allowance,
        auto_credit=auto,
        cadence=cadence,
    )
    return {"account_id": payload.account_id}


async def envelope_update_describe(
    db: AsyncSession, payload: EnvelopeUpdatePayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    account, meta = await _envelope(db, payload.account_id)
    allowance, cadence, auto = _merged(meta, payload)
    rows = [ChangeDisplayRow(label="Envelope", value=account.name)]
    if (allowance, cadence) != (meta.monthly_credit, meta.cadence):
        was = _allowance(meta.monthly_credit, meta.cadence)
        now = _allowance(allowance, cadence)
        # "$10.00 a week → $15.00 a week" says the period twice.
        if cadence == meta.cadence and meta.monthly_credit is not None:
            was = format_usd(meta.monthly_credit)
        rows.append(ChangeDisplayRow(label="Allowance", value=f"{was} → {now}"))
    if auto != meta.auto_credit:
        rows.append(
            ChangeDisplayRow(
                label="Credited",
                value="automatically each period" if auto else "by hand only",
            )
        )
    if len(rows) == 1:
        raise ValueError(f"This would change nothing about {account.name}.")
    rows.append(ChangeDisplayRow(label="Money", value=_NO_MONEY))
    return rows


class EnvelopeBalancePayload(BaseModel):
    """What is really in an envelope, when the record has drifted.

    Live: "she just told me she has $10 in", and the only card could
    change the allowance, not what is in it (2026-09-23). Recorded as
    the difference, through the same walk a credit or a spend takes, so
    the history says what was corrected and by how much.
    """

    model_config = ConfigDict(extra="forbid")

    account_id: int
    balance_cents: int = Field(ge=0)
    note: str | None = None


async def envelope_balance_execute(
    db: AsyncSession, payload: EnvelopeBalancePayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.finance.service import FinanceService

    account, _meta = await _envelope(db, payload.account_id)
    await FinanceService(db).walk_envelope(
        payload.account_id,
        delta=payload.balance_cents - (account.current_balance or 0),
        owner_user_id=owner_user_id,
        when=None,
        note=payload.note or "Balance corrected",
    )
    return {"account_id": payload.account_id, "balance_cents": payload.balance_cents}


async def envelope_balance_describe(
    db: AsyncSession, payload: EnvelopeBalancePayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    account, _meta = await _envelope(db, payload.account_id)
    standing = account.current_balance or 0
    if standing == payload.balance_cents:
        raise ValueError(
            f"This would change nothing: {account.name} holds that already."
        )
    rows = [
        ChangeDisplayRow(label="Envelope", value=account.name),
        ChangeDisplayRow(
            label="Balance",
            value=f"{format_usd(standing)} → {format_usd(payload.balance_cents)}",
        ),
    ]
    if payload.note:
        rows.append(ChangeDisplayRow(label="Because", value=payload.note))
    rows.append(ChangeDisplayRow(label="Money", value=_NO_MONEY))
    return rows
