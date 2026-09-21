"""Turning what a document says about itself into a card.

The seam ST-08 is about: extraction ends, a reading begins, and what it
found lands in the same approval queue as every other proposed write.
Nothing here touches the document.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.log import logger
from app.services.documents.domains.reading.figures import propose_figure
from app.services.documents.domains.reading.findings import Page
from app.services.documents.domains.reading.identity import identify, known_strings
from app.services.documents.domains.reading.metadata import read_document
from app.services.documents.domains.reading.titles import (
    compose,
    quotes_reference,
    still_unnamed,
    whose_letterhead,
)

if TYPE_CHECKING:
    from app.services.documents.domains.reading.letters import LetterReading

# What reads a letter's demands: pages in, a reading out. Injected so a
# test can hand over a fake one and the worker can hand over None.
LetterReader = Callable[[list[Page]], Awaitable["LetterReading"]]

METADATA = "document.metadata"
CONTACT = "contact.create"
FIGURE = "fact.record"
REQUEST = "document.request"
# Whose cards these are, so a reading's work is visible as its own.
PROPOSED_BY = "reading"


async def _already_asked(db: AsyncSession, change_type: str, document_id: int) -> bool:
    """A pending card for this document is the answer already waiting.
    Reading again must not stack a second one on top of it."""
    from app.services.finance.domains.writes.queue import list_changes

    return any(
        change.payload.get("document_id") == document_id
        for change in await list_changes(db, status="pending")
        if change.change_type == change_type
    )


async def read_and_propose(
    db: AsyncSession, document_id: int, *, owner_user_id: int | None = None
) -> None:
    """Put what a document says about itself in front of somebody.

    The half of "read a document" that is not reading it, and the one
    both doors must take. The worker's job called it and the API's
    inline extract did not, so a document read one way named itself and
    the same document read the other way went quiet - and three
    documents on the real shelf were read with nothing to show for it
    (2026-09-19).

    Guarded, because the pages just read are the valuable thing: a
    reading that falls over must not take them with it.
    """
    from app.services.documents.domains.reading.letters import letter_reader

    try:
        await propose_reading(
            db,
            document_id,
            owner_user_id=owner_user_id,
            read_letter=await letter_reader(),
        )
    except Exception:
        logger.exception("Reading %s proposed nothing", document_id)


async def propose_reading(
    db: AsyncSession,
    document_id: int,
    *,
    owner_user_id: int | None = None,
    read_letter: LetterReader | None = None,
) -> Any | None:
    """Read a document and put what it says in front of somebody.

    Two readings, and each stands on its own. What the document says
    about ITSELF is read by pattern, always. What it DEMANDS is read by
    a model, and only when the document is filed on a matter - a request
    with no matter has nothing to be a request on, and a model call on
    every scrap of paper is a bill nobody agreed to.
    """
    stranger = await _propose_contact(db, document_id, owner_user_id)
    figure = await propose_figure(db, document_id, owner_user_id, asked=_already_asked)
    request = await _propose_demands(
        db, document_id, owner_user_id=owner_user_id, read_letter=read_letter
    )
    metadata = await _propose_metadata(db, document_id, owner_user_id)
    return metadata or stranger or figure or request


async def _propose_demands(
    db: AsyncSession,
    document_id: int,
    *,
    owner_user_id: int | None,
    read_letter: LetterReader | None,
) -> Any | None:
    """What the letter asks for, as one card on its matter."""
    from app.services.documents.domains.reading.letters import checked
    from app.services.documents.queries import pages_for
    from app.services.finance.domains.writes.queue import propose
    from app.services.matters.matters import MatterService
    from app.services.matters.requests import RequestService

    if read_letter is None or await _already_asked(db, REQUEST, document_id):
        return None
    matter_id = await MatterService(db).for_document(document_id)
    if matter_id is None or await RequestService(db).citing(document_id):
        return None
    pages: list[Page] = [
        {"page": page.page_number, "text": page.text}
        for page in await pages_for(db, document_id)
    ]
    reading = checked(await read_letter(pages), pages)
    if reading is None:
        return None
    return await propose(
        db,
        REQUEST,
        {
            "document_id": document_id,
            "matter_id": matter_id,
            "received_on": reading.received_on.isoformat()
            if reading.received_on
            else None,
            "due_on": reading.due_on.isoformat() if reading.due_on else None,
            "items": [item.model_dump() for item in reading.items],
            "dropped": reading.dropped,
        },
        owner_user_id=owner_user_id,
        proposed_by_agent=PROPOSED_BY,
    )


async def _propose_metadata(
    db: AsyncSession, document_id: int, owner_user_id: int | None
) -> Any | None:
    """What the document says about itself.

    ``None`` when there is nothing to say: no findings, nothing the
    document does not already record, or a card still awaiting an answer.
    """
    from app.services.documents.queries import pages_for
    from app.services.documents.service import DocumentService
    from app.services.finance.domains.writes.queue import propose

    document = await DocumentService(db).get(document_id)
    if document is None or await _already_asked(db, METADATA, document_id):
        return None
    pages = await pages_for(db, document_id)
    payload: dict[str, Any] = {"document_id": document_id}
    findings = read_document(
        [{"page": page.page_number, "text": page.text} for page in pages]
    )
    for found in findings:
        # A card that changes nothing wastes a decision.
        standing = getattr(document, found.field, None)
        if str(standing or "") == str(found.value):
            continue
        payload[found.field] = {
            "value": str(found.value),
            "page": found.page,
            "because": found.because,
        }
    read: list[Page] = [{"page": page.page_number, "text": page.text} for page in pages]
    letterhead = await _letterhead(db, read)
    # What else the front page says that the app can name: the account a
    # statement is for, and the sender where only their phone or website
    # is printed.
    printed = identify(read, await known_strings(db))
    if title := _proposed_title(document, letterhead, payload, findings):
        payload["title"] = title
    if sender := await _proposed_sender(db, document, letterhead, printed):
        payload["sender"] = sender
    if case := await _proposed_matter(db, document, read):
        payload["matter"] = case
    if held := await _proposed_account(db, document, printed):
        payload["account"] = held
    if len(payload) == 1:
        return None
    return await propose(
        db,
        METADATA,
        payload,
        owner_user_id=owner_user_id,
        proposed_by_agent=PROPOSED_BY,
    )


async def _letterhead(db: AsyncSession, read: list[Page]) -> Any:
    """Whose paper this is, of the organizations already on file.

    Read once and used twice: the name a document gets and the contact
    it is filed under are the same reading, and reading it twice is how
    a card comes to say two different things about one letterhead.
    """
    from app.services.matters.service import PartyService

    parties = await PartyService(db).find(kind="organization")
    found = whose_letterhead(read, [party.name for party in parties])
    if found is None:
        return None
    party = next((p for p in parties if p.name == found.value), None)
    return (found, party) if party is not None else None


def _proposed_title(
    document: Any,
    letterhead: Any,
    payload: dict[str, Any],
    findings: list[Any],
) -> dict[str, Any] | None:
    """A name for a document that arrived with a filename for one.

    Only then: a title somebody typed is not ours to improve. Built from
    what this same card already carries - the kind and the date read off
    the front - plus the organization on the letterhead. Nothing is
    invented, and nothing costs a model call: a title is prose, but
    every part of it is a reading somebody could check.
    """
    if not still_unnamed(document.title, document.filename) or letterhead is None:
        return None
    said = compose(
        letterhead[0],
        next((f for f in findings if f.field == "kind"), None) or document.kind,
        (payload.get("document_date") or {}).get("value") or document.document_date,
    )
    if said is None or said.value == document.title:
        return None
    # The page it was read from and the line on it: the same citation
    # every other reading owes, and what makes the name checkable rather
    # than one to take on faith.
    return {"value": str(said.value), "page": said.page, "because": said.because}


async def _proposed_sender(
    db: AsyncSession, document: Any, letterhead: Any, printed: list[Any]
) -> dict[str, Any] | None:
    """Who wrote it, as a contact id - so approving the card files the
    document under them.

    Drop a letter in and the app should say who it is from rather than
    wait to be asked. Only an organization already on file: guessing a
    sender off a letterhead is how a second address book starts, and a
    stranger stays a stranger until somebody names them.
    """
    from app.services.documents.service import DocumentService
    from app.services.matters.models import party_tag

    if letterhead is None:
        # No name across the top, but perhaps their number or their
        # website: the Delta Dental flyer's first line is
        # deltadentalins.com and its name never appears as words.
        letterhead = await _printed_sender(db, printed)
    if letterhead is None:
        return None
    found, party = letterhead
    # Already filed under them: a card that changes nothing wastes a
    # decision.
    if party_tag(party.id) in await DocumentService(db).tags_for(document.id):
        return None
    return {"value": str(party.id), "page": found.page, "because": found.because}


async def _proposed_matter(
    db: AsyncSession, document: Any, read: list[Page]
) -> dict[str, Any] | None:
    """The case this paper quotes the number of.

    Drop a letter in and the app should say which case it belongs to,
    because the county writes the number on every page it sends. Exact
    and therefore checkable - nothing is inferred from a name or a date,
    since two cases about one person are two cases and only the number
    says which.
    """
    from app.services.matters.matters import MatterService

    matters = MatterService(db)
    if await matters.for_document(document.id) is not None:
        return None
    found = quotes_reference(
        read,
        [(one.id, one.reference or "") for one in await matters.find()],
    )
    if found is None:
        return None
    matter_id, finding = found
    return {"value": str(matter_id), "page": finding.page, "because": finding.because}


async def _propose_contact(
    db: AsyncSession, document_id: int, owner_user_id: int | None
) -> Any | None:
    """Offer to file the organization on the letterhead, when the app
    knows it and the address book does not.

    The letterhead reader only ever looked at contacts, so paper from
    American Express, citi and GreenSky named nobody - while all three
    sat in the payee directory or the institution list the whole time
    (2026-09-19). It looks in every drawer now, and where a brand is
    known but not filed, it offers to file it rather than quietly
    matching nothing.

    Never invented: a name has to be in one of those directories
    already. A letterhead read off a page and taken as an organization
    is how a second address book starts.
    """
    from app.services.documents.queries import pages_for
    from app.services.finance.domains.writes.queue import propose

    if await _already_asked(db, CONTACT, document_id):
        return None
    read: list[Page] = [
        {"page": page.page_number, "text": page.text}
        for page in await pages_for(db, document_id)
    ]
    # One sender per piece of paper. A Chase statement offered to create
    # a contact called "Chase" while "JPMorgan Chase Bank, N.A." was
    # already in the address book: the letterhead matches a payee and a
    # contact at once, and the contact is the one that counts
    # (2026-09-19).
    if await _letterhead(db, read) is not None:
        return None
    found = whose_letterhead(read, await _brands(db))
    if found is None:
        return None
    return await propose(
        db,
        CONTACT,
        {
            "name": str(found.value),
            "kind": "organization",
            "note": f"Read off a document, page {found.page}: {found.because}",
        },
        owner_user_id=owner_user_id,
        proposed_by_agent=PROPOSED_BY,
    )


async def _brands(db: AsyncSession) -> list[str]:
    """Every organization the app knows that the address book does not.

    Three directories, because they all hold the same kind of thing -
    who money and paper come from - and a household does not think of
    them as three.
    """
    from app.services.finance.domains.ledger import queries
    from app.services.finance.domains.ledger.merchants import list_merchants
    from app.services.matters.service import PartyService

    known = {
        party.name.casefold()
        for party in await PartyService(db).find(kind="organization")
    }
    institutions = await queries.institutions_for_owner(db, owner_user_id=None)
    payees = await list_merchants(db)
    return [
        name
        for name in (
            *[one.name for one in institutions],
            *[one.name for one in payees],
        )
        if name and name.casefold() not in known
    ]


async def _printed_sender(db: AsyncSession, printed: list[Any]) -> Any:
    """The contact whose phone or website is on the front page.

    Weaker evidence than a name across the top, which is why it is only
    asked when the name is absent: two readings of one sender must not
    disagree, and the name is the one a person would give.
    """
    from app.services.matters.service import PartyService

    for known, finding in printed:
        if known.kind != "party":
            continue
        party = await PartyService(db).get(known.target)
        if party is not None:
            return finding, party
    return None


async def _proposed_account(
    db: AsyncSession, document: Any, printed: list[Any]
) -> dict[str, Any] | None:
    """The account a statement says it is for.

    "Account ending 3639" is the household's own last four, printed the
    way every statement prints it - so the paper lands on the account
    without anybody choosing from a list of twenty.
    """
    from app.services.documents.service import DocumentService
    from app.services.finance.constants import account_tag

    filed = await DocumentService(db).tags_for(document.id)
    for known, finding in printed:
        if known.kind != "account" or account_tag(known.target) in filed:
            continue
        return {
            "value": str(known.target),
            "page": finding.page,
            "because": finding.because,
        }
    return None
