"""Schema helpers shared by every model module."""

from __future__ import annotations


def one_of(column: str, values: tuple[str, ...]) -> str:
    """A CHECK clause spelled from the tuple the app writes, so the
    constraint cannot drift from the values. A migration is still what
    CHANGES a constraint; this is what keeps the model honest about it."""
    allowed = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({allowed})"
