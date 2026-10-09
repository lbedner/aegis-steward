"""Seed rows: insert what is missing by key, never touch what is there.

Startup seeds (currencies, import profiles, agents, memory modules, tool
rows) all share one rule: a row whose key is already present is left
alone, because everything seeded is editable afterwards and a reboot must
not undo that. One query finds what is present; the rest is added.
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import ColumnElement
from sqlmodel import Session, SQLModel, col, select


def missing_rows(
    session: Session,
    model: type[SQLModel],
    key: str,
    rows: Sequence[dict[str, Any]],
    *where: ColumnElement[bool],
) -> list[dict[str, Any]]:
    """The rows whose ``key`` value is not yet in ``model``'s table.

    ``where`` narrows what counts as present (system import profiles share
    names with user-owned ones, so only ownerless rows count).
    """
    column = col(getattr(model, key))
    wanted = [row[key] for row in rows]
    present = set(session.exec(select(column).where(column.in_(wanted), *where)).all())
    return [row for row in rows if row[key] not in present]


def seed_rows(
    session: Session,
    model: type[SQLModel],
    key: str,
    rows: Sequence[dict[str, Any]],
    *where: ColumnElement[bool],
) -> int:
    """Add the missing rows (see ``missing_rows``); the caller commits."""
    missing = missing_rows(session, model, key, rows, *where)
    session.add_all([model(**row) for row in missing])
    return len(missing)
