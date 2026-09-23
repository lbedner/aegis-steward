"""What a document says it IS: the period it covers, and its kind.

Both are read off the front, because that is where paper introduces
itself - a dateline, a letterhead, a heading. Page nine of a policy
saying "Statement Date" is boilerplate, not this document's own date.
"""

from __future__ import annotations

from collections.abc import Iterable
import re

from app.services.documents.domains.reading.findings import Finding, Page, mostly
from app.services.documents.domains.reading.patterns import find_date, has_date
from app.services.documents.models import TAX_FORMS

# How far in to look. A document introduces itself immediately or not at
# all, and reading further is how a boilerplate date becomes a claim.
OPENING_PAGES = 2

# A date only counts when the page says what it IS. Either it follows one
# of these, or it stands alone on its line the way a dateline does.
DATE_LABELS = (
    "as of",
    "statement date",
    "date of statement",
    "statement period",
    "period ending",
    "billing date",
    "invoice date",
    "date issued",
    "issue date",
    "notice date",
    "date of this notice",
    "dated",
)
_LABEL = re.compile(r"\b(" + "|".join(DATE_LABELS) + r")\b\s*:?", re.I)

# A heading that names the kind. Only the kinds a heading can be trusted
# to mean: "other" is never proposed, because it is the default already
# and a card that changes nothing wastes a decision.
#
# The words come off real paper. Four documents sat unnamed on the shelf
# saying "Application", "Welcome to Delta Dental" and "Combined Contract
# and Disclosure Form" in their first line - unread because this
# vocabulary knew two kinds out of seven, not because the paper was
# silent (2026-09-19).
KIND_MARKERS: tuple[tuple[str, str], ...] = (
    ("statement", "statement"),
    ("explanation of benefits", "statement"),
    ("account summary", "statement"),
    ("amortization schedule", "schedule"),
    ("payment schedule", "schedule"),
    ("payment plan", "schedule"),
    ("application", "form"),
    ("enrollment form", "form"),
    ("disclosure form", "form"),
    ("claim form", "form"),
    # What somebody writes TO you when you join: prose with a greeting,
    # which is a letter however the sender brands it.
    ("welcome to", "letter"),
)

# How much a heading may say BESIDES the kind. "Combined Contract and
# Disclosure Form" is a heading; "Did you know this statement is
# available electronically" is marketing prose that happens to contain
# the word - and it filed a Delta Dental claim statement correctly by
# luck, which is the failure this surface exists to avoid. A heading IS
# the kind; a sentence only mentions it.
HEADING_EXTRA_WORDS = 3
_MARKER = re.compile(
    r"\b(" + "|".join(re.escape(m) for m, _ in KIND_MARKERS) + r")\b", re.I
)
# "Dear Mr. Bedner:" - the one thing only a letter does.
_SALUTATION = re.compile(r"^dear\b[^\n]{0,60}[:,]\s*$", re.I)

# A kind is read from a HEADING, not from a field label: a short line
# with no date in it. Otherwise "Statement Date: March 3" would make
# every dated page a statement.
HEADING_CHARS = 60


# Tax paper names its form in a heading - "Form 1099-INT" - or, when the
# form number is artwork, by a box caption only that form prints. The
# Citizens 1098's text layer has no "1098" in it at all; box 1's caption
# is there (#138, 2026-09-14).
_FORM = re.compile(
    r"\b(?:form\s+)?("
    + "|".join(re.escape(f) for f in sorted(TAX_FORMS, key=len, reverse=True))
    + r")(?![\w-])",
    re.I,
)
FORM_CAPTIONS: tuple[tuple[str, str], ...] = (
    ("mortgage interest received from payer/borrower", "1098"),
)
# The year it is FOR, which the paper labels. The label and the year can
# sit on separate lines: "For calendar year" / "2025".
_TAX_YEAR = re.compile(
    r"\b(?:for\s+)?(?:tax|calendar)\s+year\s*:?\s*((?:19|20)\d{2})\b", re.I
)


def _lines(pages: Iterable[Page]) -> Iterable[tuple[int, str]]:
    """Every line of the opening pages, with the page it sits on."""
    for page in list(pages)[:OPENING_PAGES]:
        for line in (page["text"] or "").splitlines():
            if stripped := line.strip():
                yield page["page"], stripped


def _dated(lines: list[tuple[int, str]]) -> Finding | None:
    for page, line in lines:
        if found := find_date(line):
            when, written = found
            if written == line:  # a dateline: the line IS the date
                return Finding("document_date", when, page, line)
        if label := _LABEL.search(line):
            if after := find_date(line, after=label.end()):
                return Finding("document_date", after[0], page, line)
    return None


def _kind(lines: list[tuple[int, str]]) -> Finding | None:
    for page, line in lines:
        if _SALUTATION.match(line):
            return Finding("kind", "letter", page, line)
        if len(line) <= HEADING_CHARS and not has_date(line):
            if marker := _MARKER.search(line):
                said = marker.group(1).lower()
                if mostly(line, said):
                    kind = next(k for m, k in KIND_MARKERS if m == said)
                    return Finding("kind", kind, page, line)
    return None


def _form(lines: list[tuple[int, str]]) -> Finding | None:
    for page, line in lines:
        said = line.casefold()
        for caption, form in FORM_CAPTIONS:
            if caption in said:
                return Finding("form_type", form, page, line)
        if len(line) <= HEADING_CHARS and (named := _FORM.search(line)):
            if mostly(line, named.group(0)):
                form = next(
                    f for f in TAX_FORMS if f.casefold() == named.group(1).casefold()
                )
                return Finding("form_type", form, page, line)
    return None


def _tax_year(pages: Iterable[Page]) -> Finding | None:
    for page in list(pages)[:OPENING_PAGES]:
        if found := _TAX_YEAR.search(page["text"] or ""):
            # The label and the year quoted as one line, however it wrapped.
            said = " ".join(found.group(0).split())
            return Finding("tax_year", int(found.group(1)), page["page"], said)
    return None


def read_document(pages: Iterable[Page]) -> list[Finding]:
    """What this document says about itself, each finding carrying the
    page and the line it was read from. Nothing certain, nothing said.

    Tax paper is known by its FORM, and only then is a year read: "plan
    year 2026" on a benefits statement is not a tax year."""
    pages = list(pages)
    lines = list(_lines(pages))
    if form := _form(lines):
        kind = Finding("kind", "tax", form.page, form.because)
        return [
            found
            for found in (_dated(lines), kind, form, _tax_year(pages))
            if found is not None
        ]
    return [found for found in (_dated(lines), _kind(lines)) if found is not None]
