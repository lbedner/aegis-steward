"""What a reader hands back."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, TypedDict

# How much a line may say BESIDES the thing it names. "Combined Contract
# and Disclosure Form" is a heading; "Did you know this statement is
# available electronically" is prose that happens to contain the word.
# "AMERICAN EXPRESS" is a letterhead; "Your Target Date Fund 2045
# allocation changed" is not a letter from Target (2026-09-19).
EXTRA_WORDS = 3


def mostly(line: str, phrase: str) -> bool:
    """True when the line IS the phrase rather than mentioning it.

    Measured in words left over once the phrase is taken out, because
    that is the difference a reader sees: a heading or a letterhead
    carries a qualifier or two, a sentence carries a subject, a verb and
    an object.
    """
    rest = re.sub(re.escape(phrase), " ", line, flags=re.I)
    return len([word for word in rest.split() if word.strip(":-·|,.")]) <= EXTRA_WORDS


def flat(text: str | None) -> str:
    """Text with its whitespace collapsed and its case dropped, so a
    quote is compared by what it SAYS rather than how it wrapped.

    Here rather than in either reader: a PDF's text layer breaks lines
    where the page does, and every reader that checks a claim against a
    page needs the same answer to "is this line on it".
    """
    return re.sub(r"\s+", " ", text or "").strip().casefold()


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
