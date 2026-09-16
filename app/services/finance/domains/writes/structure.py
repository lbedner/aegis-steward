"""Structural change types: what a row IS. A split carves one purchase
into category lines; a match records which payment paid which bill.
The hub module ``executors`` registers these."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.detection.insights.formatting import format_usd
from app.services.finance.domains.ledger import categories, splits
from app.services.finance.domains.writes.display import txn_row
from app.services.finance.schemas import ChangeDisplayRow, SplitPart


class MatchPayload(BaseModel):
    """Which payment paid which bill."""

    model_config = ConfigDict(extra="forbid")

    transaction_id: int
    stream_id: int


async def match_execute(
    db: AsyncSession, payload: MatchPayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.finance.domains.planning.recurring import streams

    stream = await streams.attach_transaction_to_stream(
        db,
        payload.transaction_id,
        payload.stream_id,
        owner_user_id=owner_user_id,
    )
    if stream is None:
        raise ValueError(
            f"Transaction {payload.transaction_id} or bill "
            f"{payload.stream_id} not found."
        )
    return {
        "transaction_id": payload.transaction_id,
        "stream_id": stream.id,
        "next_expected_date": (
            stream.next_expected_date.isoformat() if stream.next_expected_date else None
        ),
    }


async def match_describe(
    db: AsyncSession, payload: MatchPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    from app.services.finance.domains.planning.recurring import streams

    txn, subject = await txn_row(db, payload.transaction_id, owner_user_id)
    stream = await streams.get_recurring(db, payload.stream_id, owner_user_id)
    # A match is a MOVE too: from whichever live bill holds the row now
    # (usually none) to the proposed one, read from the row so the card
    # shows the current truth.
    before = "Unmatched"
    if txn is not None and txn.recurring_stream_id is not None:
        holder = await streams.get_recurring(db, txn.recurring_stream_id, owner_user_id)
        if holder is not None:
            before = holder.name
    after = stream.name if stream is not None else f"bill {payload.stream_id} (missing)"
    return [
        subject.model_copy(update={"label": "Payment"}),
        ChangeDisplayRow(label="Bill", value=f"{before} \u2192 {after}"),
    ]


class SplitChangePayload(BaseModel):
    """Which transaction, which lines. Parts follow the ``SplitPart``
    contract - positive magnitudes in cents; the service signs them and
    fills the difference as a remainder line under the parent's own
    category, so a proposal only states what the agent knows."""

    model_config = ConfigDict(extra="forbid")

    transaction_id: int
    parts: list[SplitPart]

    @field_validator("parts")
    @classmethod
    def _parts_are_positive_magnitudes(cls, parts: list[SplitPart]) -> list[SplitPart]:
        """Propose-time, not approve-time: a card that can never execute
        must never exist, and the error loops back to the proposer."""
        if not parts:
            raise ValueError("a split needs at least one part")
        if any(part.amount <= 0 for part in parts):
            raise ValueError(
                "part amounts are positive magnitudes in cents; the "
                "transaction's own sign is applied automatically"
            )
        return parts


async def split_execute(
    db: AsyncSession, payload: SplitChangePayload, owner_user_id: int | None
) -> dict[str, Any]:
    lines = await splits.split_transaction(
        db, payload.transaction_id, payload.parts, owner_user_id=owner_user_id
    )
    return {
        "transaction_id": payload.transaction_id,
        "line_count": len(lines),
        "amounts": [line.amount for line in lines],
    }


async def split_describe(
    db: AsyncSession, payload: SplitChangePayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    txn, subject = await txn_row(db, payload.transaction_id, owner_user_id)
    wanted = [p.category_id for p in payload.parts if p.category_id is not None]
    if txn is not None and txn.category_id is not None:
        wanted.append(txn.category_id)
    names = await categories.category_names(db, wanted)
    # One card row PER LINE - the user reviews the itemization the way
    # it will land: category, amount, and what the amount covers.
    rows = [subject]
    for part in payload.parts:
        value = format_usd(part.amount)
        if part.memo:
            value += f" · {part.memo}"
        rows.append(
            ChangeDisplayRow(
                label=names.get(part.category_id, "Uncategorized"), value=value
            )
        )
    # Show the remainder line the approval will actually create - the
    # card must promise exactly what the executor does.
    if txn is not None:
        remainder = abs(txn.amount) - sum(p.amount for p in payload.parts)
        if remainder > 0:
            parent_name = (
                names.get(txn.category_id, "Uncategorized")
                if txn.category_id is not None
                else "Uncategorized"
            )
            rows.append(
                ChangeDisplayRow(
                    label=parent_name,
                    value=f"{format_usd(remainder)} · the rest",
                )
            )
    return rows


# --- Declaring a stream ---------------------------------------------------
#
# The one act Illiana could not do: "call it $90 a week and be done with
# it". She could match a payment to a bill that exists; she could not
# say a new one exists. Same act as the Bills page's "declare recurring",
# proposed as a card and approved like every other write.

DIRECTIONS = ("inflow", "outflow")


class DeclarePayload(BaseModel):
    """A new recurring stream: income or a bill, its rhythm and amount."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    direction: str
    frequency: str
    amount_cents: int = Field(gt=0)
    next_expected_date: date
    account_id: int | None = None
    # Stated ABOUT THE STREAM and stopping there, the way the Bills page
    # sets it: its transactions keep their own categories.
    category_id: int | None = None
    subject_id: int | None = None
    is_subscription: bool = False

    @field_validator("direction")
    @classmethod
    def _known_direction(cls, value: str) -> str:
        if value not in DIRECTIONS:
            raise ValueError(f"direction must be one of {', '.join(DIRECTIONS)}")
        return value

    @field_validator("frequency")
    @classmethod
    def _known_frequency(cls, value: str) -> str:
        from app.services.finance.constants import BILL_FREQUENCY_OPTIONS

        if value not in BILL_FREQUENCY_OPTIONS:
            raise ValueError(
                f"frequency must be one of {', '.join(BILL_FREQUENCY_OPTIONS)}"
            )
        return value


async def declare_execute(
    db: AsyncSession, payload: DeclarePayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.finance.domains.planning.recurring import streams

    stream = await streams.create_recurring_stream(
        db,
        owner_user_id=owner_user_id,
        name=payload.name,
        direction=payload.direction,
        frequency=payload.frequency,
        expected_amount=payload.amount_cents,
        next_expected_date=payload.next_expected_date,
        account_id=payload.account_id,
        is_subscription=payload.is_subscription,
        subject_id=payload.subject_id,
    )
    if payload.category_id is not None:
        stream.category_id = payload.category_id
        db.add(stream)
    await db.flush()
    return {"stream_id": stream.id, "name": stream.name}


async def declare_describe(
    db: AsyncSession, payload: DeclarePayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    """The card: what, which way, how often, how much, from when."""
    from app.services.finance.constants import frequency_label
    from app.services.finance.domains.detection.insights.formatting import format_usd

    rows = [
        ChangeDisplayRow(label="Stream", value=payload.name),
        ChangeDisplayRow(
            label="Direction",
            value="Income" if payload.direction == "inflow" else "Bill",
        ),
        ChangeDisplayRow(label="Every", value=frequency_label(payload.frequency)),
        ChangeDisplayRow(label="Amount", value=format_usd(payload.amount_cents)),
        ChangeDisplayRow(
            label="Starting", value=payload.next_expected_date.isoformat()
        ),
    ]
    if payload.account_id is not None:
        from app.services.finance.domains.ledger.queries.accounts import account_by_id

        account = await account_by_id(
            db, payload.account_id, owner_user_id=owner_user_id
        )
        rows.append(
            ChangeDisplayRow(label="Account", value=account.name if account else "-")
        )
    if payload.category_id is not None:
        from app.services.finance.models import FinanceCategory

        category = await db.get(FinanceCategory, payload.category_id)
        rows.append(
            ChangeDisplayRow(label="Category", value=category.name if category else "-")
        )
    return rows
