"""How to reach somebody, read off the paper already on the shelf.

The address, the phone and the website were printed on the letterhead
before anybody asked for them. Dutchess County DSS puts its street
address and its switchboard at the top of every letter it sends; Delta
Dental prints a claims number and a contact URL on every invoice. The
workflow files that paper first, so the shelf is the cheapest lookup
there is, and its provenance is a document and a page rather than a
model's recollection.

PATTERNS, NEVER A MODEL. A page that yields nothing yields nothing. A
guessed phone number is worse than a blank field: the blank one is
visibly missing and the wrong one is not, and nobody re-checks a number
that looks plausible until a call fails.

Nothing here writes. It offers candidates, each carrying the document
and page it was read from, and ``contact.amend`` is the only door into
the record.
"""

from __future__ import annotations

import re
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.matters.reach import CONTACT_FIELDS, CONTACT_LINES

# Letterhead and footer carry the contact block. A number buried on page
# nine of a policy belongs to whatever that page is about, not to the
# sender - so the scan stops early rather than growing more clever.
OPENING_PAGES = 2

# A phone number as paper prints it, and NOT a case number: the run has
# to be grouped the way a person writes a phone, because "23548895152718"
# and "MA258760XX" are on the same letters and neither is a way to reach
# anyone. Requires the 3-3-4 shape with real separators or parentheses.
_PHONE = re.compile(
    r"(?<![\w-])(?:\+?1[.\s-])?(?:\(\d{3}\)\s?|\d{3}[.\s-])\d{3}[.\s-]\d{4}(?![\w-])"
)

# A host with a real TLD, optionally with a path. Bare enough to catch
# "www.dutchessny.gov/dcfs" and "deltadentalins.com" without swallowing a
# sentence, and an email is excluded by the lookbehind.
_URL = re.compile(
    r"(?<![@\w])(?:https?://)?(?:www\.)?"
    r"[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:gov|com|org|net|edu|us)"
    r"(?:/[^\s,;)]*)?",
    re.IGNORECASE,
)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# The last line of a US postal address: "POUGHKEEPSIE, NY 12601". The
# anchor for an address, because the CITY, ST ZIP line is the only part
# with a shape worth trusting; the street is taken from above it.
_CITY_STATE_ZIP = re.compile(
    r"^\s*([A-Za-z][A-Za-z.\s'-]{1,40}),?\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\s*$"
)

# A street line: a number, then words. "60 MARKET STREET", "419 N.
# Quaker Lane". Deliberately not trying to know every suffix - a line
# that starts with a house number and sits directly above a CITY, ST ZIP
# is an address by position, which is more reliable than a word list.
_STREET = re.compile(r"^\s*\d+[A-Za-z]?\s+[A-Za-z0-9.\s'#-]{3,60}$")


def _host(url: str) -> str:
    """The hostname of a printed URL, lowercased and stripped of www."""
    host = re.sub(r"^https?://", "", url.strip(), flags=re.IGNORECASE)
    host = host.split("/")[0].lower()
    return host[4:] if host.startswith("www.") else host


def _registrable(domain: str) -> str:
    """The last two labels: ``mail.dutchessny.gov`` -> ``dutchessny.gov``.

    Crude on purpose. A real public-suffix list is a dependency and a
    cache for a check whose only job is to refuse a stranger's gmail.
    """
    parts = domain.strip(".").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain


def _titled(line: str) -> str:
    """Letterheads SHOUT. Give it back the way a person would write it,
    but leave anything already mixed-case alone - "McDonald" survives
    and "MARKET STREET" stops shouting."""
    return line.title() if line.isupper() else line


def _address_in(lines: list[str]) -> str | None:
    """A street line directly above a CITY, ST ZIP line.

    Position rather than vocabulary. The pair is what makes it an
    address; either half alone is a sentence that happens to have a
    number in it.
    """
    for index, line in enumerate(lines):
        if not _CITY_STATE_ZIP.match(line):
            continue
        city, state, postcode = _CITY_STATE_ZIP.match(line).groups()  # type: ignore[union-attr]
        tail = f"{_titled(city.strip())}, {state} {postcode}"
        above = lines[index - 1].strip() if index else ""
        if above and _STREET.match(above):
            return f"{_titled(above.strip())}, {tail}"
    return None


def found_in(text: str) -> dict[str, str]:
    """The reach fields a page offers, keyed by ``CONTACT_FIELDS`` name.

    The FIRST match of each kind wins, because a letterhead leads. A
    letter with a switchboard at the top and an examiner's direct line
    in the body must not promote the direct line into ``phone``: that
    field is what you ring when you have nothing else, and the labelled
    lines belong in ``also`` where a person names them.
    """
    if not text:
        return {}
    lines = text.splitlines()
    found: dict[str, str] = {}

    if match := _PHONE.search(text):
        found["phone"] = match.group(0).strip()

    # Take the emails OUT before looking for a website. An address
    # carries a domain inside it, and a lookbehind does not save you:
    # "Traver@dfa.state.ny.us" matched from "state.ny.us" onwards,
    # because the character before it is a dot. That produced a website
    # of "state.ny.us" which then corroborated the very email it had
    # been cut from - two wrong fields propping each other up, both
    # cited to a real page (live, 2026-09-18).
    emails = _EMAIL.findall(text)
    without_emails = _EMAIL.sub(" ", text)

    if match := _URL.search(without_emails):
        found["website"] = match.group(0).rstrip(".,;)")
    if address := _address_in(lines):
        found["address"] = address

    # An email on a letter is usually the RECIPIENT'S. Found on the real
    # shelf: a Delta Dental notice carried the enrollee's personal gmail
    # address, and reading it as Delta Dental's would have filed a
    # household member's private address under an insurer.
    #
    # So an email is only believed when the letterhead corroborates it:
    # its domain must match the site printed on the same page. Nothing
    # to corroborate against means nothing offered, which is the right
    # answer for a field this easy to get wrong.
    if emails and (site := found.get("website")):
        # A sentence's full stop is not part of the address.
        email = emails[0].rstrip(".,;:)>")
        address_domain = email.rsplit("@", 1)[-1].lower()
        # EQUAL registrable domains, not endswith: "notdeltadentalins.com"
        # ends with "deltadentalins.com", and a suffix test on a hostname
        # accepts exactly the lookalike it is meant to refuse.
        if _registrable(_host(site)) == _registrable(address_domain):
            found["email"] = email

    known = {key for key, _label in CONTACT_FIELDS}
    return {key: value for key, value in found.items() if key in known and value}


# A label and the rest of its line: "SSPA-45 fax: 845-486-3301" gives
# ("fax", "845-486-3301"). The label is the WORD before the colon, not
# the whole prefix, because forms carry a form number in front of it.
_LABELLED = re.compile(r"(?:^|\s)([A-Za-z][A-Za-z ]{0,20}?)\s*:\s*(\S.*)$")


def lines_in(text: str, besides: set[str] | None = None) -> list[tuple[str, str]]:
    """The labelled ways to reach somebody that a page prints.

    Everything the four ``CONTACT_FIELDS`` have no room for: a fax, an
    examiner's direct line, a caseworker's address, a Spanish line. A
    number nobody labelled is a number whose meaning is lost, so a line
    needs BOTH halves and an unlabelled one is dropped rather than
    guessed at.

    The value is the rest of the line, VERBATIM. A person reads these and
    nothing parses them, so an OCR artifact shows as printed rather than
    being quietly closed up - "Melissa. Traver@..." is a reading somebody
    can see is imperfect, and repairing it here would be a guess wearing
    a citation.

    ``besides`` are values already taken as a main field, so the
    letterhead's own number is not offered twice.
    """
    taken = {value.strip() for value in (besides or set())}
    found: list[tuple[str, str]] = []
    for line in (text or "").splitlines():
        if not (_PHONE.search(line) or _EMAIL.search(line)):
            continue
        match = _LABELLED.search(line.strip())
        if match is None:
            continue
        label, value = match.group(1).strip(), match.group(2).strip()
        if not label or value in taken or _digits(value) in {
            _digits(one) for one in taken
        }:
            continue
        found.append((label, value))
    return found


def _digits(value: str) -> str:
    """Just the digits, so "(845) 486-3000" and "845-486-3000" are the
    same number printed two ways."""
    return re.sub(r"\D", "", value)


async def contact_details(db: AsyncSession, party_id: int) -> list[dict[str, Any]]:
    """What this party's own paper says about reaching them.

    Only what the record LACKS. A field already filled in is not
    re-offered: a card that changes nothing is a card nobody reads, and
    ``contact.amend`` refuses one anyway.

    Each offer carries its document and page, so the card can cite the
    line rather than assert it. An offer that cannot say where it came
    from is not returned.
    """
    from sqlmodel import col, select

    from app.services.documents.models import Document, DocumentPage, DocumentTag
    from app.services.matters.models import party_tag
    from app.services.matters.service import PartyService

    party = await PartyService(db).get(party_id)
    if party is None:
        return []
    have = {key for key, value in (party.contact or {}).items() if value}

    documents = list(
        (
            await db.exec(
                select(DocumentTag.document_id)
                .join(Document, col(Document.id) == col(DocumentTag.document_id))
                .where(DocumentTag.label == party_tag(party_id))
                .where(col(Document.deleted_at).is_(None))
                .order_by(col(DocumentTag.document_id))
            )
        ).all()
    )
    if not documents:
        return []

    pages = (
        await db.exec(
            select(DocumentPage)
            .where(col(DocumentPage.document_id).in_(documents))
            .where(col(DocumentPage.page_number) <= OPENING_PAGES)
            .order_by(col(DocumentPage.document_id), col(DocumentPage.page_number))
        )
    ).all()

    from app.services.matters.reach import reach_lines

    # What the record already prints, so a line is not offered twice.
    # Compared on digits as well as text: "(845) 486-3000" and
    # "845-486-3000" are one number written two ways.
    already = {value for _label, value in reach_lines(party.contact)}
    already_digits = {_digits(one) for one in already if _digits(one)}

    offers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in pages:
        text = page.text or ""
        main = found_in(text)
        for field, value in main.items():
            if field in have or field in seen:
                continue
            seen.add(field)
            offers.append(
                {
                    "field": field,
                    "value": value,
                    "document_id": page.document_id,
                    "page": page.page_number,
                }
            )
        # Then everything the four fields have no room for, labelled.
        # The main values are held back so the letterhead's own number
        # is a field rather than a line as well.
        for label, value in lines_in(text, besides=set(main.values()) | already):
            if value in seen or _digits(value) in already_digits:
                continue
            seen.add(value)
            offers.append(
                {
                    "field": CONTACT_LINES,
                    "label": label,
                    "value": value,
                    "document_id": page.document_id,
                    "page": page.page_number,
                }
            )
    return offers
