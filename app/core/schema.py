"""Schema helpers shared by every model module."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def one_of(column: str, values: tuple[str, ...]) -> str:
    """A CHECK clause spelled from the tuple the app writes, so the
    constraint cannot drift from the values. A migration is still what
    CHANGES a constraint; this is what keeps the model honest about it."""
    allowed = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({allowed})"


def require_one_of(value: str, values: tuple[str, ...]) -> str:
    """``value`` if the tuple allows it; otherwise the one refusal every
    service gives, naming what would have been accepted."""
    if value not in values:
        raise ValueError(f"One of: {', '.join(values)}.")
    return value


def known(values: tuple[str, ...]) -> Callable[[Any, str | None], str | None]:
    """A Pydantic field validator for a field that must be one of
    ``values`` when given: ``_known_kind = field_validator("kind")(known(KINDS))``.
    None passes, so an optional field needs no second rule."""

    def validate(cls: Any, value: str | None) -> str | None:
        return None if value is None else require_one_of(value, values)

    return validate
