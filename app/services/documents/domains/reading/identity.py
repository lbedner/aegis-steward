"""What the front of a document says about itself that is not its name.

A letterhead is not the only thing on a page this household already
knows. A statement prints the account it is for; a letter prints the
sender's phone and website; a bank prints its routing number. Every one
of those is a string the app already stores - on an account, on a
contact's reach block, on the institution row - and none of them was
being read (2026-09-19).

Same shape as the letterhead reader, deliberately: a value on FILE,
matched against the front of the page, citing the line it was read from.
Nothing is invented and nothing is inferred from a name.

The rules differ per kind because the risk does. A domain or a routing
number is unique enough to stand alone; a four-digit account mask is
not - a statement is full of four-digit numbers, and only the ones
printed AS an account number mean anything.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re
from typing import Any

from app.services.documents.domains.reading.findings import Finding, Page, flat
from app.services.documents.domains.reading.metadata import OPENING_PAGES


@dataclass(frozen=True)
class Known:
    """One identifying string already on file, and what it identifies.

    ``how`` decides the rule, because a mask and a domain are not
    matched the same way and pretending otherwise is how a balance of
    $3,639.00 becomes an account number.
    """

    kind: str
    target: int
    how: str
    value: str


# "ending 3639", "ending in 3639", "****3639", "XXXX3639", "Acct #3639".
# The label is the point: four digits with nothing claiming they are an
# account are four digits.
_MASK = re.compile(
    r"(?:account|acct|card)\b[^0-9\n]{0,24}(\d{4})\b"
    r"|(?:ending(?:\s+in)?|[*x·•]{2,}|#)\s*(\d{4})\b",
    re.I,
)
_NINE = re.compile(r"\b(\d{9})\b")

# An account number printed bare, with no label anywhere near it - which
# is how a real Chase statement prints it, on every page. Eight digits
# or more ending in the last four on file is not a year, an amount or a
# coincidence; four digits alone still are (2026-09-19).
_LONG_NUMBER = re.compile(r"\b\d{8,}\b")


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def _domain(value: str) -> str:
    """A website as it is COMPARED: no scheme, no www., no path."""
    said = flat(value).removeprefix("https://").removeprefix("http://")
    return said.removeprefix("www.").split("/", 1)[0].strip()


def _hit(known: Known, line: str) -> bool:
    if known.how == "mask":
        labelled = any(
            digits == known.value
            for match in _MASK.finditer(line)
            for digits in match.groups()
            if digits
        )
        return labelled or any(
            found.endswith(known.value) for found in _LONG_NUMBER.findall(line)
        )
    if known.how == "domain":
        return _domain(known.value) in _domain(line) or _domain(known.value) in flat(
            line
        )
    if known.how == "phone":
        # Compared on digits, and on the last ten of them: a letterhead
        # writes 1-845-486-3000 where the address book wrote
        # (845) 486-3000, and both are one telephone.
        wanted = _digits(known.value)[-10:]
        return bool(wanted) and wanted in _digits(line)
    if known.how == "routing":
        return any(found == known.value for found in _NINE.findall(line))
    return False


def identify(
    pages: Iterable[Page], known: Iterable[Known]
) -> list[tuple[Known, Finding]]:
    """Everything on the front of this document that the app can name.

    One hit per known thing: a phone printed in the header and again in
    the footer is one sender, not two.
    """
    front = [
        (page["page"], line.strip())
        for page in list(pages)[:OPENING_PAGES]
        for line in (page["text"] or "").splitlines()
        if line.strip()
    ]
    found: list[tuple[Known, Finding]] = []
    seen: set[tuple[str, int, str]] = set()
    for page, line in front:
        for one in known:
            key = (one.kind, one.target, one.how)
            if key in seen or not _hit(one, line):
                continue
            seen.add(key)
            found.append((one, Finding(one.how, one.value, page, line)))
    return found


async def known_strings(db: Any) -> list[Known]:
    """Every identifying string this household already stores.

    Four sources, because a household does not think of them as four -
    it thinks "who is this from, and what is it about". An account's
    last four, a contact's website and phone, an institution's routing
    number.

    Read once per document and matched in memory: four small queries
    beat a query per line of every page.
    """
    from app.services.finance.domains.ledger import queries
    from app.services.matters.reach import reach_lines
    from app.services.matters.service import PartyService

    known: list[Known] = []

    accounts, _total = await queries.accounts_page(
        db, owner_user_id=None, include_hidden=True, page=1, page_size=500,
        subject_id=queries.EVERYONE,
    )
    known.extend(
        Known("account", account.id, "mask", account.mask)
        for account in accounts
        if account.mask
    )

    for party in await PartyService(db).find():
        contact = party.contact or {}
        if website := contact.get("website"):
            known.append(Known("party", party.id, "domain", str(website)))
        # The main number AND the labelled ones: a county office prints
        # its caseworker's direct line as readily as its switchboard.
        numbers = [contact.get("phone"), *[value for _label, value in reach_lines(contact)]]
        known.extend(
            Known("party", party.id, "phone", str(number))
            for number in numbers
            if number and len(_digits(str(number))) >= 10
        )

    known.extend(
        Known("institution", bank.id, "routing", bank.routing_number)
        for bank in await queries.institutions_for_owner(db, owner_user_id=None)
        if bank.routing_number
    )
    return known
