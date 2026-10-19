"""Every edit, logged, and the last one undone (#325).

Who changed what on a transaction - you, an assistant, an import, a sync
- with the value before and after, one row per field, in the reserved
``finance_transaction_changelog``. One action is one batch (a request, an
approved card or batch of cards, an import), and undoing takes the newest
batch back as a whole.

Recorded at the session, before each flush, so no edit path can forget:
inside ``acting(...)`` every change to a field people set is written down;
outside one (detection's bookkeeping, a migration) nothing is. Fields
the app keeps for itself - which bill a row belongs to, when it was
touched - are not edits and are not logged.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime
import functools
from typing import Any, TypeVar
import uuid

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger import queries
from app.services.finance.models import (
    FinanceTransaction,
    FinanceTransactionChangelog,
    FinanceTransactionTag,
)
from app.services.finance.utils import utcnow

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])

# Who is acting, and the action their changes belong to.
_actor: ContextVar[str | None] = ContextVar("finance_change_actor", default=None)
_batch: ContextVar[str | None] = ContextVar("finance_change_batch", default=None)

# The fields people set, as the history names them. A tag is a link
# row, logged as field "tag" with its id coming (new) or going (old).
LABELS = {
    "date_": "Date",
    "name": "Description",
    "amount": "Amount",
    "account_id": "Account",
    "merchant_id": "Payee",
    "category_id": "Category",
    "memo": "Memo",
    "excluded_from_reports": "Left out of reports",
    "is_reviewed": "Reviewed",
    "deleted_at": "Removed",
    "tag": "Tag",
}
_TYPES: dict[str, type] = {
    "date_": date,
    "amount": int,
    "account_id": int,
    "merchant_id": int,
    "category_id": int,
    "excluded_from_reports": bool,
    "is_reviewed": bool,
    "deleted_at": datetime,
    "tag": int,
}
UNDO = "undo"
REDO = "redo"
IMPORT = "import"
SYNC = "sync"
# Arrivals are history, not decisions: Undo takes back what you or an
# assistant did, never a feed that landed after it. An undo or a redo is
# a move along that history, not an action of its own.
_NOT_UNDOABLE = {UNDO, REDO, IMPORT, SYNC}
STEPS = {UNDO, REDO}
# Session key: this flush's changes another action already stands for.
_QUIET = "finance_changelog_quiet"


@contextmanager
def acting(actor: str, *, join: bool = False) -> Iterator[str]:
    """Log what happens inside as ``actor``'s, as one action: a fresh
    batch, restored to the outer one after (an approval inside a request
    is the assistant's, not the request's). ``join`` keeps the outer
    batch when the same actor already holds one: each card approved in a
    batch is that batch's action."""
    current = _batch.get()
    if join and current and _actor.get() == actor:
        yield current
        return
    actor_token = _actor.set(actor)
    batch = str(uuid.uuid4())
    batch_token = _batch.set(batch)
    try:
        yield batch
    finally:
        _batch.reset(batch_token)
        _actor.reset(actor_token)


def as_actor(actor: str) -> Callable[[F], F]:
    """``acting(actor, join=True)`` around a whole coroutine function: an
    import, a sync."""

    def wrap(fn: F) -> F:
        @functools.wraps(fn)
        async def run(*args: Any, **kwargs: Any) -> Any:
            with acting(actor, join=True):
                return await fn(*args, **kwargs)

        return run  # type: ignore[return-value]

    return wrap


def act_as(actor: str) -> None:
    """``acting`` for the rest of this context - a request, which has no
    block to wrap: its edits are one action, the request's own."""
    if _actor.get() is None:
        _actor.set(actor)
        _batch.set(str(uuid.uuid4()))


def quietly(db: AsyncSession, rows: Iterable[FinanceTransaction], field: str) -> None:
    """The next flush's change to ``field`` on ``rows`` is bookkeeping of
    an action logged elsewhere - a merge's payee, a settled pair's report
    flags - and is not logged: an Undo of it would take back half the
    action and leave the rest (charges on a payee merged away)."""
    quiet = db.sync_session.info.setdefault(_QUIET, set())
    quiet.update((id(row), field) for row in rows)


def encode(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, date | datetime):
        return value.isoformat()
    return str(value)


def decode(field: str, text: str | None) -> Any:
    if text is None:
        return None
    kind = _TYPES.get(field, str)
    if kind is bool:
        return text == "true"
    if kind is date:
        return date.fromisoformat(text)
    if kind is datetime:
        return datetime.fromisoformat(text)
    return kind(text)


def _row(
    transaction_id: int, owner: int | None, field: str, old: Any, new: Any
) -> FinanceTransactionChangelog:
    return FinanceTransactionChangelog(
        transaction_id=transaction_id,
        owner_user_id=owner or 0,
        field=field,
        old_value=encode(old),
        new_value=encode(new),
        change_source=_actor.get() or "",
        batch_id=_batch.get(),
    )


@event.listens_for(Session, "before_flush")
def _record(session: Session, _context: Any, _instances: Any) -> None:
    """Write down what this flush changes, when somebody is acting."""
    quiet = session.info.pop(_QUIET, set())
    if _actor.get() is None:
        return
    logged: list[FinanceTransactionChangelog] = []
    for obj in session.dirty:
        if not isinstance(obj, FinanceTransaction) or obj.id is None:
            continue
        state = inspect(obj)
        for field in LABELS.keys() - {"tag"}:
            if (id(obj), field) in quiet:
                continue
            history = state.attrs[field].history
            if not history.has_changes():
                continue
            old = history.deleted[0] if history.deleted else None
            new = history.added[0] if history.added else None
            if encode(old) != encode(new):
                logged.append(_row(obj.id, obj.owner_user_id, field, old, new))
    for obj, coming in [(o, True) for o in session.new] + [
        (o, False) for o in session.deleted
    ]:
        if isinstance(obj, FinanceTransactionTag):
            tag = obj.tag_id
            logged.append(
                _row(
                    obj.transaction_id,
                    None,
                    "tag",
                    None if coming else tag,
                    tag if coming else None,
                )
            )
    session.add_all(logged)


def note_bulk(
    db: AsyncSession, changed: list[tuple[int, int | None, Any]], field: str, new: Any
) -> None:
    """Log a change made by one UPDATE statement, which no flush sees:
    ``changed`` is each row's ``(id, owner, old value)``."""
    if _actor.get() is None:
        return
    db.add_all(
        [
            _row(txn_id, owner, field, old, new)
            for txn_id, owner, old in changed
            if encode(old) != encode(new)
        ]
    )


async def history(
    db: AsyncSession, transaction_id: int
) -> list[FinanceTransactionChangelog]:
    """One transaction's changes, newest first."""
    return await queries.changes_where(
        db, FinanceTransactionChangelog.transaction_id == transaction_id
    )


async def recent(
    db: AsyncSession, owner_user_id: int | None, limit: int = 400
) -> list[FinanceTransactionChangelog]:
    """The newest changes, newest first: the log grouped into actions."""
    return await queries.changes_where(
        db,
        FinanceTransactionChangelog.owner_user_id == (owner_user_id or 0),
        limit=limit,
    )


async def last_undoable(db: AsyncSession, owner_user_id: int | None) -> str | None:
    """The newest action that can still be taken back: not undone, and
    not an undo itself (undoing an undo is just the edit again), nor an
    import or a sync - however many of those landed after it."""
    rows = await queries.changes_where(
        db,
        FinanceTransactionChangelog.owner_user_id == (owner_user_id or 0),
        FinanceTransactionChangelog.batch_id.is_not(None),  # type: ignore[union-attr]
        FinanceTransactionChangelog.undone_at.is_(None),  # type: ignore[union-attr]
        FinanceTransactionChangelog.change_source.not_in(_NOT_UNDOABLE),  # type: ignore[attr-defined]
        limit=1,
    )
    return rows[0].batch_id if rows else None


async def last_redoable(db: AsyncSession, owner_user_id: int | None) -> str | None:
    """The action undone last, while nothing you or an assistant did
    since stands on top of it - a redo over a newer edit would reapply a
    stale change, so a new action ends the chance."""
    owner = FinanceTransactionChangelog.owner_user_id == (owner_user_id or 0)
    undone_at = FinanceTransactionChangelog.undone_at
    rows = await queries.changes_where(
        db,
        owner,
        undone_at.is_not(None),  # type: ignore[union-attr]
        limit=1,
        order=undone_at.desc(),  # type: ignore[union-attr]
    )
    if not rows or rows[0].undone_at is None:
        return None
    since = await queries.changes_where(
        db,
        owner,
        FinanceTransactionChangelog.changed_at > rows[0].undone_at,
        FinanceTransactionChangelog.change_source.not_in(_NOT_UNDOABLE),  # type: ignore[attr-defined]
        limit=1,
    )
    return None if since else rows[0].batch_id


async def undo(db: AsyncSession, owner_user_id: int | None) -> tuple[int, int]:
    """Take back the newest action: each field to its value before it,
    unless it has changed again since (that field is left alone).
    Returns ``(fields put back, fields left)``. The undo is logged too."""
    batch = await last_undoable(db, owner_user_id)
    return await _step(db, batch, back=True) if batch else (0, 0)


async def redo(db: AsyncSession, owner_user_id: int | None) -> tuple[int, int]:
    """Put back the action undone last - the undo, applied the other way."""
    batch = await last_redoable(db, owner_user_id)
    return await _step(db, batch, back=False) if batch else (0, 0)


async def _step(db: AsyncSession, batch: str, *, back: bool) -> tuple[int, int]:
    """Move one action's fields from one side of it to the other: back to
    each ``old_value`` (undo) or forward to each ``new_value`` (redo),
    only where a field still holds the side it is moved from."""
    rows = await queries.changes_where(
        db, FinanceTransactionChangelog.batch_id == batch
    )
    txns = await queries.transactions_by_ids(db, list({r.transaction_id for r in rows}))
    tags = {(t.transaction_id, t.tag_id): t for t in await queries.tags_on(db, txns)}
    moved = left = 0
    with acting(UNDO if back else REDO):
        for row in rows:
            source, target = (
                (row.new_value, row.old_value)
                if back
                else (row.old_value, row.new_value)
            )
            txn = txns.get(row.transaction_id)
            if txn is None:
                left += 1
                continue
            if row.field == "tag":
                key = (txn.id, decode("tag", row.new_value or row.old_value))
                if source is not None and key in tags:
                    await db.delete(tags.pop(key))
                elif target is not None and key not in tags:
                    db.add(FinanceTransactionTag(transaction_id=key[0], tag_id=key[1]))
                moved += 1
                continue
            if encode(getattr(txn, row.field)) != source:
                left += 1
                continue
            setattr(txn, row.field, decode(row.field, target))
            db.add(txn)
            moved += 1
        undone = utcnow() if back else None
        for row in rows:
            row.undone_at = undone
            db.add(row)
        await db.flush()
    return moved, left
