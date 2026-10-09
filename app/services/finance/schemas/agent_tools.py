"""What the agent's finance tools return, as types.

Code mode prints each tool's return type in the signature the model
codes against. Typed ``dict[str, Any]``, every tool read ``-> dict``, so
her scripts guessed keys - all eight of one call read
``bill_candidates(...)['transactions']`` (the key was 'candidates'),
got nothing, and she proposed ids she made up. A key lives here once.
Each ``*_cents`` number also arrives with its ``*_usd`` beside it
(``with_dollars``, after the tool returns, so declared NotRequired):
display text, never computed on.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict


class TransactionRow(TypedDict):
    id: int  # what a proposal's 'transaction_id' takes
    date: str
    payee: str | None
    amount_cents: int  # signed, negative = outflow
    amount_usd: NotRequired[str]
    category: str | None
    category_id: int | None
    uncategorized: bool
    account: str | None
    tags: list[str]
    memo: str | None
    transfer: bool
    pending: bool
    bill_id: int | None  # the bill (``bills``' id) it is matched to


class Transactions(TypedDict):
    total: int
    returned: int
    transactions: list[TransactionRow]
    error: NotRequired[str]  # a filter that names nothing (an unknown tag)


class Bill(TypedDict):
    id: int  # what a proposal's 'stream_id' takes
    name: str | None
    direction: str
    frequency: str
    amount_cents: int | None
    amount_usd: NotRequired[str]
    amount_is_declared: bool
    account: str | None
    account_id: int | None
    category: str | None
    next_expected_date: str | None
    last_date: str | None


class Bills(TypedDict):
    bills: list[Bill]


class BillWithCandidates(TypedDict):
    stream_id: int
    name: str | None
    due_date: str | None
    candidates: list[TransactionRow]  # likeliest first


class BillCandidates(TypedDict):
    bills: list[BillWithCandidates]


class DueBill(TypedDict):
    name: str
    date: str  # when the walk pays it (an overdue bill lands today)
    due_date: str | None
    amount_cents: int
    amount_usd: NotRequired[str]
    category: str | None
    bill_id: int | None


class ProjectionPoint(TypedDict):
    date: str
    name: str
    direction: str
    amount_cents: int
    amount_usd: NotRequired[str]
    balance_cents: int
    balance_usd: NotRequired[str]
    account: str | None
    category: str | None
    bill_id: int | None  # null for a non-bill point


class Projection(TypedDict):
    as_of: str
    horizon_days: int
    start_balance_cents: int
    start_balance_usd: NotRequired[str]
    end_balance_cents: int
    end_balance_usd: NotRequired[str]
    upcoming_total_cents: int
    upcoming_total_usd: NotRequired[str]
    bills: list[DueBill]
    points: list[ProjectionPoint]
