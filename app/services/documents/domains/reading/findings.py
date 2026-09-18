"""What a reader hands back."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict


class Page(TypedDict):
    """A read page, as a reader sees it. Deliberately not the ORM row:
    a reader that needs a database cannot be tested against a string."""

    page: int
    text: str | None


@dataclass(frozen=True)
class Finding:
    """One thing a document says about itself.

    ``because`` is the point of the whole exercise: the line the value
    was read off, quoted, so the card can show its working and a person
    can disagree with it in a glance.
    """

    field: str
    value: Any
    page: int
    because: str
