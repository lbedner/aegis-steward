"""The primitives every reader shares: finding a date in a line of text.

Conservative on purpose. Anything these cannot parse with certainty is
not a finding, and a document with no findings is a document somebody
looks at themselves.
"""

from __future__ import annotations

from datetime import date
import re

MONTHS = {
    m: n
    for n, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for m in names
}
# Named, not private: a filename made of a month is the same
# vocabulary read for a different question (2026-09-19).
_MONTHS = MONTHS
_MONTH_WORDS = "|".join(sorted(MONTHS, key=len, reverse=True))

# Each pattern yields (year, month, day) through ``_BUILDERS``. A shape
# nobody writes deliberately is left out: two-digit years are read as
# 20xx because a household's paper is not from the nineteen-hundreds.
_SHAPES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b"), "mdy"),
    (
        re.compile(rf"\b({_MONTH_WORDS})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.I),
        "month_first",
    ),
    (
        re.compile(rf"\b(\d{{1,2}})\s+({_MONTH_WORDS})\.?,?\s+(\d{{4}})\b", re.I),
        "day_first",
    ),
)


def _built(shape: str, parts: tuple[str, ...]) -> date | None:
    """A real date, or None when the numbers cannot mean one."""
    try:
        if shape == "ymd":
            y, m, d = (int(p) for p in parts)
        elif shape == "mdy":
            m, d, y = (int(p) for p in parts)
            y += 2000 if y < 100 else 0
        elif shape == "month_first":
            m, d, y = _MONTHS[parts[0].lower()], int(parts[1]), int(parts[2])
        else:
            d, m, y = int(parts[0]), _MONTHS[parts[1].lower()], int(parts[2])
        return date(y, m, d)
    except (ValueError, KeyError):
        return None


def find_date(text: str, after: int = 0) -> tuple[date, str] | None:
    """The first real date at or after ``after``, with the text it was
    written as. ``None`` when the line holds no date anybody could mean."""
    best: tuple[int, date, str] | None = None
    for pattern, shape in _SHAPES:
        for match in pattern.finditer(text):
            if match.start() < after:
                continue
            found = _built(shape, match.groups())
            if found is not None and (best is None or match.start() < best[0]):
                best = (match.start(), found, match.group(0))
            break
    return (best[1], best[2]) if best else None


def has_date(text: str) -> bool:
    return find_date(text) is not None
