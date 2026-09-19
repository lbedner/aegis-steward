"""The figure a statement prints about itself.

The county asked for a balance as of 1 August. The register could offer
what it believed and say plainly that nothing proved it - and the
statement that WOULD prove it sat on the shelf, read, with the number on
page one and nobody reading it (2026-09-19).

A labelled line, the way every other reading here works. A statement
prints "New balance as of 09/07/26: $7,857.27" the same way every month,
and that line carries all three things a fact needs: the figure, the day
it is as of, and the words to quote for having said so.

Only lines that SAY they are a balance. A statement is full of money -
a minimum payment, a credit limit, a late fee - and a minimum payment
read as a balance is a wrong number on a benefits form, which is worse
than the blank it replaced.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
import re
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.documents.domains.reading.findings import Page
from app.services.documents.domains.reading.identity import identify, known_strings
from app.services.documents.domains.reading.metadata import OPENING_PAGES
from app.services.documents.domains.reading.patterns import find_date

# The card this reading proposes, and whose work it is - the same two
# names every reading here files under.
FIGURE = "fact.record"
PROPOSED_BY = "reading"

# What a statement calls the number that answers "how much is in it".
# Deliberately short: every word here is one an agency would accept as
# the balance, and a word that is only nearly one belongs to a person to
# add rather than to this list to guess.
BALANCE_LABELS = (
    "new balance",
    "ending balance",
    "closing balance",
    "current balance",
    "statement balance",
    "account balance",
    "balance as of",
)
_LABEL = re.compile(r"\b(" + "|".join(BALANCE_LABELS) + r")\b", re.I)

# "$7,857.27", "1,204.00", "-$230.00" - the money on the line, as
# printed, parsed by the app's own money reader rather than a second
# one. It must carry a currency sign or cents: "New balance as of
# 09/07/26" has a date in it, and a bare "09" read as money makes the
# balance nine dollars.
_MONEY = re.compile(r"-?\$\s?\d[\d,]*(?:\.\d{2})?|-?\d[\d,]*\.\d{2}")


@dataclass(frozen=True)
class Balance:
    """A balance a document states, and where it was read."""

    value_cents: int
    as_of: date | None
    page: int
    because: str


def balances(pages: Iterable[Page]) -> list[Balance]:
    """The balance this document states, or nothing.

    One figure: a statement repeats its balance in the summary and again
    in the detail, and two readings of one number is two facts about one
    thing. The first printing wins, because a statement leads with what
    it is about.
    """
    from app.components.web_frontend.filters import money_to_cents

    for page in list(pages)[:OPENING_PAGES]:
        for line in (page["text"] or "").splitlines():
            said = line.strip()
            if not said or not _LABEL.search(said):
                continue
            # The money AFTER the label: "Balance as of 09/07/26:
            # $7,857.27" has a date in it that is not money, and the
            # label itself may carry digits.
            tail = said[_LABEL.search(said).end() :]
            money = _MONEY.search(tail)
            if money is None:
                continue
            cents = money_to_cents(money.group().replace(" ", ""))
            if cents is None:
                continue
            found = find_date(said)
            return [Balance(cents, found[0] if found else None, page["page"], said)]
    return []


async def propose_figure(
    db: AsyncSession,
    document_id: int,
    owner_user_id: int | None,
    *,
    asked: Any,
) -> Any | None:
    """The balance a statement states, as a fact about whoever holds the
    account it is for.

    The county asked for a balance as of a date; the register could only
    say what it believed. The statement that PROVES it was on the shelf,
    read, with the number on page one.

    Three things have to line up, and none of them is guessed: the
    document says it is a balance, the page says which account by its
    last four, and that account is held in somebody's name. Our own
    accounts propose nothing - a fact is about SOMEBODY, and a card
    asking whose this is would be a question rather than a proposal.
    """
    from app.services.documents.queries import pages_for
    from app.services.finance.domains.ledger.subjects import get_subject
    from app.services.finance.domains.writes.queue import propose
    from app.services.matters.service import PartyService

    if await asked(db, FIGURE, document_id):
        return None
    read: list[Page] = [
        {"page": page.page_number, "text": page.text}
        for page in await pages_for(db, document_id)
    ]
    found = balances(read)
    if not found:
        return None
    account = await _account_on(db, read)
    if account is None or not account.subject_id:
        return None
    subject = await get_subject(db, account.subject_id)
    party = (
        await PartyService(db).get(subject.party_id)
        if subject and subject.party_id
        else None
    )
    if party is None:
        return None

    balance = found[0]
    document = await _document_row(db, document_id)
    as_of = balance.as_of or (document.document_date if document else None)
    return await propose(
        db,
        FIGURE,
        {
            "subject_party_id": party.id,
            "attribute": "account_balance",
            "account_id": account.id,
            "value_cents": balance.value_cents,
            "as_of": as_of.isoformat() if as_of else None,
            "label": account.name,
            # Cited the way a figure has to be: what proves it, the page
            # it is on, and the line it was read from.
            "provenance": "document",
            "document_id": document_id,
            "page": balance.page,
            "source_note": balance.because,
        },
        owner_user_id=owner_user_id,
        proposed_by_agent=PROPOSED_BY,
    )


async def _account_on(db: AsyncSession, read: list[Page]) -> Any:
    """The account this paper says it is about, by its printed last four."""
    from app.services.finance.domains.ledger.queries.accounts import account_by_id

    for known, _finding in identify(read, await known_strings(db)):
        if known.kind == "account":
            return await account_by_id(db, known.target)
    return None


async def _document_row(db: AsyncSession, document_id: int) -> Any:
    from app.services.documents.service import DocumentService

    return await DocumentService(db).get(document_id)
