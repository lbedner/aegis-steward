"""Naming a document the way a person would file it.

Paper arrives named after a file. "20260826-statements-3639-.pdf" is
what the bank called the download: it tells a reader nothing, it cannot
be asked for out loud, and it is why a conversation about a document
falls back to its id - a thing nobody knows or should have to
(2026-09-18).

NO MODEL. A title looks like prose, which is what made reaching for one
tempting, but every part of it is already read: ``metadata`` finds the
KIND and the DATE off the front page by pattern, and the address book
already holds the organizations that write to this household. A name is
those three joined - "Hudson Valley Credit Union statement, August
2026" - and joining them is exact, free, and explainable to the person
approving it.

Nothing is invented. The organization has to be one already on file and
its name has to appear ON the page; without that there is no title, and
no title is a fine answer.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
import re

from app.services.documents.domains.reading.findings import Finding, Page, flat, mostly
from app.services.documents.domains.reading.metadata import OPENING_PAGES

# As long as a person would write, and no longer: a title is read in a
# list beside forty others, and one that wraps is one that hides the next.
MAX_TITLE = 70

# A heading longer than this is a sentence wearing a heading's clothes,
# and a title has to fit beside forty others.
_MAX_HEADING = 44

# What a document IS, in the word a person would use for it. "letter"
# reads oddly in a title where the sender is already named - "Delta
# Dental letter, August 2026" is what somebody would write - so the
# kinds pass through as they are, and a document with no kind read off
# it is named without one.
_UNTITLED_KINDS = frozenset({"other"})

_EXTENSION = re.compile(r"\.[a-z0-9]{2,5}$", re.I)


def still_unnamed(title: str, filename: str | None) -> bool:
    """True when nobody has named this document yet.

    Exact where it can be: a title that IS the filename it arrived as is
    a document nobody has named. The heuristic below is the fallback for
    rows that predate the filename column - their original is gone, and
    a shelf of them must still be nameable.
    """
    if filename:
        return flat(title) == flat(filename)
    return looks_like_a_filename(title)


def looks_like_a_filename(title: str) -> bool:
    """True when the title is what the FILE was called rather than what
    the document IS - the only case worth proposing a name for.

    The test is the stem, not the extension. "NYSLRS Monthly Statement.pdf"
    is a name somebody typed that happens to carry ".pdf", and renaming
    it is noise; "20260826-statements-3639-.pdf" and
    "ppo_policy_document" are what a bank's download, a scanner's counter
    and a phone's camera roll produce. Words with spaces between them is
    what a person writes and a filesystem discourages, so that is the
    line.
    """
    said = (title or "").strip()
    if not said:
        return True
    stem = _EXTENSION.sub("", said).strip()
    if " " not in stem:
        return True
    # A DATE with a space in it is still what the bank called the file:
    # "September 07.pdf" is nobody's idea of a name, and reading it as
    # one left the document unnamed on the real shelf (2026-09-19).
    return _is_mostly_a_date(stem)


def whose_letterhead(pages: Iterable[Page], names: Iterable[str]) -> Finding | None:
    """The organization on the front of this document, of the ones
    already on file.

    The FIRST line that names one wins, not the longest name in the
    document: an insurer mentioned on page two of a bank statement is a
    party to something, not the sender of this. Within a line the
    longest name wins, so "Hudson Valley Credit Union" beats a shorter
    row that is a prefix of it.

    And the line has to BE the name. A directory of a hundred payees
    will match somebody in the prose of any long document - "Your Target
    Date Fund 2045 allocation changed" is not a letter from Target.

    Only the opening pages, for the reason the date is read off the
    front: page nine of a policy naming an insurer is boilerplate.
    """
    front = [
        (page["page"], line.strip())
        for page in list(pages)[:OPENING_PAGES]
        for line in (page["text"] or "").splitlines()
        if line.strip()
    ]
    on_file = sorted(
        {n.strip() for n in names if n and n.strip()}, key=len, reverse=True
    )
    for page, line in front:
        for name in on_file:
            if _names(line, name) and mostly(line, name):
                return Finding("sender", name, page, line)
    return None


def _names(line: str, name: str) -> bool:
    """True when the LINE names this organization, not merely contains
    its letters.

    "Chase" was read off "Purchases +$0.00" and offered as a contact,
    twice. A directory of a hundred short brands finds itself inside
    ordinary words all day, so the match is at word boundaries
    (2026-09-19).
    """
    edge = r"(?<![0-9a-z])" if name[:1].isalnum() else ""
    tail = r"(?![0-9a-z])" if name[-1:].isalnum() else ""
    return re.search(edge + re.escape(flat(name)) + tail, flat(line)) is not None


def _names_the_same(organization: str, descriptor: str) -> bool:
    """True when one already says the other: the first two words of an
    organization are what a heading repeats ("Welcome to Delta Dental"),
    not its legal tail."""
    short = " ".join(organization.split()[:2])
    return flat(short) in flat(descriptor)


def _period(when: date | str | None) -> str:
    if when is None:
        return ""
    if isinstance(when, str):
        try:
            when = date.fromisoformat(when)
        except ValueError:
            return ""
    return when.strftime("%B %Y")


def _descriptor(kind: Finding | str | None) -> str:
    """What to call the paper, in its OWN words where it has them.

    A kind is a category - two Delta Dental documents both came out
    "Delta Dental of New York, Inc. form", which tells them apart no
    better than their filenames did. The heading the kind was read from
    is the distinguishing thing and it is already in hand: "Application",
    "Combined Contract and Disclosure Form" (2026-09-19).
    """
    if kind is None:
        return ""
    if isinstance(kind, str):
        return "" if kind in _UNTITLED_KINDS else kind
    if str(kind.value) in _UNTITLED_KINDS:
        return ""
    heading = _without_identifiers(str(kind.because or ""))
    return heading if heading and len(heading) <= _MAX_HEADING else str(kind.value)


# What a heading says that nobody would read aloud: "Application ID:
# 1903014447". The identifier is the one part of a name that cannot be
# recognised in a list, and it is exactly the part a filename was full
# of (2026-09-19).
# Either the number SAYS it is one ("ID: 1903014447", "#00123456") or it
# is long enough to be nothing else. A bare four digits is a year, and
# "plan year 2026" keeps its year.
_IDENTIFIER = re.compile(
    r"\s*(?:\b(?:id|no|num|number|ref|reference|account|acct)\b\s*[:#]?\s*\d[\d\s-]{3,}"
    r"|[:#]\s*\d[\d\s-]{3,}"
    r"|\b\d[\d\s-]{4,})$",
    re.I,
)


def _without_identifiers(heading: str) -> str:
    """A heading with its trailing number taken off."""
    said = " ".join((heading or "").split())
    return _IDENTIFIER.sub("", said).strip(" :-·|,")


def compose(
    letterhead: Finding | None,
    kind: Finding | str | None,
    when: date | str | None,
) -> Finding | None:
    """A name from what was already read, or nothing.

    Two things are required and neither is enough alone. Without the
    organization, "statement, August 2026" is a category. Without a kind
    or a date, the organization ALONE is a category too: run over a real
    shelf this named four different documents "Delta Dental of New York,
    Inc." - four papers with one name is the filename problem in tidier
    clothes, so the ones it cannot tell apart keep the filename and stay
    findable (2026-09-19).
    """
    if letterhead is None:
        return None
    organization = str(letterhead.value)
    period = _period(when)
    descriptor = _descriptor(kind)
    if not descriptor and not period:
        return None
    # "Delta Dental of New York, Inc. Welcome to Delta Dental" says the
    # sender twice. When the paper's own heading already names them, the
    # heading IS the title.
    if descriptor and _names_the_same(organization, descriptor):
        said = descriptor
    elif descriptor:
        said = f"{organization} {descriptor}"
    else:
        said = organization
    if period:
        said = f"{said}, {period}"
    said = " ".join(said.split())
    if not said or len(said) > MAX_TITLE:
        return None
    return Finding("title", said, letterhead.page, letterhead.because)


# A case number is quoted, not described: the county prints
# "Case MA258760XX" on every page it sends. Short references are not
# matched at all - a three-character one appears in prose by accident,
# and a document filed on the wrong case is worse than one filed on
# none.
MIN_REFERENCE = 5


def quotes_reference(
    pages: Iterable[Page], references: Iterable[tuple[int, str]]
) -> tuple[int, Finding] | None:
    """The case whose reference this paper quotes, of the ones on file.

    Exact, and therefore checkable: the agency's own number appears in
    the text or it does not. Nothing is inferred from a name, a date or
    a subject - two cases about one person are two cases, and only the
    number says which (2026-09-19).
    """
    front = [
        (page["page"], line.strip())
        for page in list(pages)[:OPENING_PAGES]
        for line in (page["text"] or "").splitlines()
        if line.strip()
    ]
    on_file = sorted(
        ((matter_id, ref.strip()) for matter_id, ref in references if ref and ref.strip()),
        key=lambda pair: len(pair[1]),
        reverse=True,
    )
    for page, line in front:
        said = flat(line)
        for matter_id, reference in on_file:
            if len(reference) >= MIN_REFERENCE and flat(reference) in said:
                return matter_id, Finding("matter", reference, page, line)
    return None


def _is_mostly_a_date(stem: str) -> bool:
    """True when the stem is nothing but a date written out.

    Not the date PARSER: "September 07" and "Aug 2026" are half a date
    each and neither parses, but both are what a bank calls a download
    rather than what a person calls a document. Months and numbers and
    nothing else is the test.
    """
    from app.services.documents.domains.reading.patterns import MONTHS

    words = [word.strip("-_.,") for word in stem.split() if word.strip("-_.,")]
    return bool(words) and all(
        word.isdigit() or word.casefold() in MONTHS for word in words
    )
