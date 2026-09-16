"""Matter host tools: the case surface Illiana works from.

The read half of ST-12. Deliberately few and wide, the same call the
finance tools made: results land in the code-mode sandbox rather than
the prompt, so one broad payload beats a catalog of question-shaped
tools.

NOT the snapshot. What is true about money is injected every turn
because almost every question touches it; a matter is relevant on the
days it is open and invisible the rest of the year, so putting it in
the snapshot would buy tokens on every message to answer a question
nobody asked.

Every write stays a proposal. There is no write tool here - working a
matter adds change types, not tools.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.core.db import get_async_session
from app.services.ai.domains.chat.pastes import document_text
from app.services.ai.domains.chat.tools import register_tool
from app.services.documents.domains.extraction.dispatch import (
    start_extraction,
    wait_for_extraction,
)
from app.services.documents.service import DocumentService
from app.services.finance.utils import current_date
from app.services.matters.facts import FactService, monthly_cents
from app.services.matters.matters import MatterService
from app.services.matters.models import FACT_ATTRIBUTES, PARTY_TAG_PREFIX, party_tag
from app.services.matters.requests import RequestService, overdue, standing
from app.services.matters.requests import titles as paper_titles
from app.services.matters.service import PartyService

ATTRIBUTE_LABELS = dict(FACT_ATTRIBUTES)


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


async def parties() -> dict[str, Any]:
    """The people and organizations the app knows about.

    Returns a dict with key 'parties': a list of entries carrying 'id',
    'name', 'kind' ("person" or "organization"), 'sort_name', 'contact'
    and 'document_ids' - the paper filed against them (a pension fund's
    statements, an agency's letters), each readable with `paper`. The
    ids are what every other matter tool reports and what a proposal's
    payload names - a party cannot be addressed by name.
    """
    from app.services.documents.queries import document_ids_by_tag_prefix

    async with get_async_session() as db:
        found = await PartyService(db).find()
        paper = await document_ids_by_tag_prefix(db, PARTY_TAG_PREFIX)
    return {
        "parties": [
            {
                "id": party.id,
                "name": party.name,
                "kind": party.kind,
                "sort_name": party.sort_name,
                "contact": party.contact or {},
                "note": party.note,
                "document_ids": paper.get(party_tag(party.id), []),
            }
            for party in found
        ]
    }


async def matters(status: str = "open") -> dict[str, Any]:
    """The cases letters belong to.

    Args:
        status: "open", "closed" or "all".

    Returns a dict with key 'matters': a list of entries carrying 'id',
    'title', 'kind', 'reference' (the agency's OWN number, which is what
    two letters agree about), 'status', 'opened_on', 'subject',
    'counterpart', 'participants' (role and party) and 'requests'
    (counts of open and total, plus 'next_due' and 'overdue_count').
    """
    wanted = None if status == "all" else status
    async with get_async_session() as db:
        service = MatterService(db)
        requests = RequestService(db)
        found = await service.find(status=wanted)
        names = {party.id: party.name for party in await PartyService(db).find()}
        rows = []
        for matter in found:
            people = await service.participants(matter.id)
            asked = await requests.for_matter(matter.id)
            today = current_date()
            standing_requests = [one for one in asked if one.status == "open"]
            due = [one.due_on for one in standing_requests if one.due_on]
            rows.append(
                {
                    "id": matter.id,
                    "title": matter.title,
                    "kind": matter.kind,
                    "reference": matter.reference,
                    "status": matter.status,
                    "opened_on": _iso(matter.opened_on),
                    "subject": names.get(matter.subject_party_id or -1),
                    "counterpart": names.get(matter.counterpart_party_id or -1),
                    "participants": [
                        {"role": link.role, "party_id": party.id, "party": party.name}
                        for link, party in people
                    ],
                    "requests": {
                        "open": len(standing_requests),
                        "total": len(asked),
                        "next_due": _iso(min(due)) if due else None,
                        "overdue_count": sum(1 for one in asked if overdue(one, today)),
                    },
                }
            )
    return {"matters": rows}


async def requests(
    matter_id: int | None = None, outstanding: bool = True
) -> dict[str, Any]:
    """What has been asked for, by when, and what still stands.

    Args:
        matter_id: Limit to one case; omit for every case.
        outstanding: True reports only requests still open.

    Returns a dict with key 'requests': a list of entries carrying 'id',
    'matter_id', 'matter', 'received_on', 'due_on', 'status', 'overdue'
    (read off the due date and today, never stored), 'settled', 'total'
    and 'items'. Each item carries 'id', 'ordinal', 'asked' (the
    sentence AS WRITTEN, which is the wording you are held to), 'ask'
    (our normalisation), 'as_of', 'status' and 'document_id' - the paper
    that answers it, or null where nothing does yet.
    """
    today = current_date()
    async with get_async_session() as db:
        service = RequestService(db)
        cases = await MatterService(db).find(status=None if matter_id else "open")
        titles = {case.id: case.title for case in cases}
        wanted = [matter_id] if matter_id else list(titles)
        rows = []
        for case_id in wanted:
            for one in await service.for_matter(case_id):
                if outstanding and one.status != "open":
                    continue
                items = await service.items(one.id)
                settled, total = standing(items)
                letter = (await paper_titles(db, [one.document_id])).get(
                    one.document_id or 0
                )
                rows.append(
                    {
                        "id": one.id,
                        "matter_id": one.matter_id,
                        "matter": titles.get(one.matter_id),
                        # The letter the asks came from: read it with `paper`.
                        "letter_document_id": one.document_id,
                        "letter": letter["title"] if letter else None,
                        "received_on": _iso(one.received_on),
                        "due_on": _iso(one.due_on),
                        "status": one.status,
                        "overdue": overdue(one, today),
                        "settled": settled,
                        "total": total,
                        "note": one.note,
                        "items": [
                            {
                                "id": item.id,
                                "ordinal": item.ordinal,
                                "asked": item.asked,
                                "ask": item.ask,
                                "as_of": _iso(item.as_of),
                                "status": item.status,
                                "resolution": item.resolution,
                                "document_id": item.document_id,
                            }
                            for item in items
                        ],
                    }
                )
    return {"requests": rows}


async def facts(
    subject_party_id: int | None = None,
    matter_id: int | None = None,
    account_id: int | None = None,
    attribute: str | None = None,
) -> dict[str, Any]:
    """What can be said about someone's money, and how it is known.

    Args:
        subject_party_id: Whose money.
        matter_id: The case the facts were gathered for.
        account_id: The account they are about - a pension's monthly
            figure, its plan, the number it is known by.
        attribute: One of gross_income, net_income, account_balance,
            resource_value, premium, other.

    Returns a dict with key 'facts': entries carrying 'id', 'subject',
    'attribute', 'label' (what the source calls it), 'value_cents',
    'period' ("once", "day", "week", "month", "year"),
    'monthly_cents' (the rate read as a month - OUR arithmetic, not the
    source's words), 'text_value', 'as_of', 'provenance' ("stated",
    "document" or "ledger"), 'document_id', 'page', 'source_note',
    'source_url' (the page it was read off) and 'verified'.

    Two facts for the same subject, attribute and date are deliberate: a
    deposit and a benefit letter disagree because GROSS income is the
    deposit plus what was withheld before it arrived. Report both with
    their sources; do not reconcile them.
    """
    async with get_async_session() as db:
        found = await FactService(db).find(
            subject_party_id=subject_party_id,
            matter_id=matter_id,
            account_id=account_id,
            attribute=attribute,
        )
        names = {party.id: party.name for party in await PartyService(db).find()}
    return {
        "facts": [
            {
                "id": fact.id,
                "subject_party_id": fact.subject_party_id,
                "subject": names.get(fact.subject_party_id),
                "matter_id": fact.matter_id,
                "account_id": fact.account_id,
                "attribute": fact.attribute,
                "attribute_label": ATTRIBUTE_LABELS.get(fact.attribute),
                "label": fact.label,
                "value_cents": fact.value_cents,
                "period": fact.period,
                "monthly_cents": monthly_cents(fact.value_cents, fact.period),
                "text_value": fact.text_value,
                "as_of": _iso(fact.as_of),
                "provenance": fact.provenance,
                "document_id": fact.document_id,
                "page": fact.page,
                "source_note": fact.source_note,
                "source_url": fact.source_url,
                "verified": fact.verified,
                "note": fact.note,
            }
            for fact in found
        ]
    }


async def paper(document_id: int) -> dict[str, Any]:
    """The text of one document on file, page by page - the letter a
    request came from, a statement attached to an ask, a fact's source.
    The ids come from `matters`, `requests` and `facts` ('document_id').

    A document already read comes straight back. One never read is
    handed to the worker to read (a scanned page with no text layer goes
    to the vision model when one is configured) and this WAITS for it -
    a few minutes for a long scan - then returns the text in the same
    call. Only if the read outlasts the wait does this return with
    'read' False and 'reading' set; then say it is still being read and
    call again. An unreadable page is named in place, so a page that
    said nothing is never mistaken for one nobody read.

    Returns 'id', 'title', 'kind', 'media_type', 'page_count', 'dated',
    'read', 'reading' (the job id, only while it is still on the worker)
    and 'text' - the pages in order, each under a '--- page N ---'
    heading.
    """
    async with get_async_session() as db:
        document = await DocumentService(db).get(document_id)
        if document is None:
            return {"error": f"No document with id {document_id}"}
        text = await document_text(document_id, db)
    reading = None
    if text is None:
        job_id = await start_extraction(document_id, owner_user_id=None, force=False)
        if await wait_for_extraction(job_id) == "running":
            reading = job_id
        else:
            async with get_async_session() as db:
                text = await document_text(document_id, db)
    return {
        "id": document.id,
        "title": document.title,
        "kind": document.kind,
        "media_type": document.media_type,
        "page_count": document.page_count,
        "dated": _iso(document.document_date),
        "read": bool(text),
        "reading": reading,
        "text": text or "",
    }


register_tool(
    "parties",
    parties,
    description="The people and organizations a matter involves, with ids",
    replace=True,
)
register_tool(
    "matters",
    matters,
    description="Cases, who is in them, and what is outstanding on each",
    replace=True,
)
register_tool(
    "requests",
    requests,
    description="What was asked for, by when, what is answered and what is missing",
    replace=True,
)
register_tool(
    "facts",
    facts,
    description="What can be said about someone's money, with where it came from",
    replace=True,
)
register_tool(
    "paper",
    paper,
    description="The text of a document on file; queued for reading if it never was",
    replace=True,
)
