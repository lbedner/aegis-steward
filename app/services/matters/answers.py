"""What to write on the county's form, and where each figure came from.

The one step in the whole matter that still happens by hand. The app
knows what was asked, what answers it and where every figure was read;
a person then re-reads all of that on screen and transcribes it into a
paper form. That transcription is where a wrong number or a skipped
item gets in, and it is the only part nothing was helping with.

So this shapes one sheet: every ask, in one of three states, each
answered one carrying the figure AND the source behind it. A figure on
a form with no source is a number somebody will be asked to justify
months later, by which time nobody remembers which statement it came
off.

EVERY ask is on the sheet, including the ones with no answer. A sheet
that lists only what is done is a sheet that hides what is not, which
is precisely how a deadline arrives with two items still outstanding.

Explicitly not form-filling and not submission. The output is a sheet a
human transcribes and sends.
"""

from __future__ import annotations

from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.matters.requests import SETTLED

# The three states an ask can be in on the sheet. "waived" and
# "not_applicable" are SETTLED, not missing: somebody said the county
# did not ask for this, and printing it as outstanding sends them
# chasing paper nobody wants.
ANSWERED = "answered"
MISSING = "missing"
WAIVED = "waived"


# Settled WITHOUT being answered, derived from the one list of settled
# statuses rather than typed out again: a fourth settled status would
# otherwise read as missing here and settled everywhere else.
NOT_ASKED_FOR = tuple(status for status in SETTLED if status != "satisfied")


def _state(status: str, has_evidence: bool) -> str:
    if status in NOT_ASKED_FOR:
        return WAIVED
    return ANSWERED if has_evidence or status == "satisfied" else MISSING


async def answer_sheet(db: AsyncSession, matter_id: int) -> dict[str, Any]:
    """The whole sheet for one matter.

    Returns the matter's own identifiers - a sheet beside a form needs
    the case number the county files it under, or it is a page about
    nothing in particular - the asks in the order they were asked, and
    a count of what is still outstanding.
    """
    from app.services.matters.evidence import satisfied_by_many
    from app.services.matters.facts import FactService
    from app.services.matters.ledger_figures import unproven_figures
    from app.services.matters.matters import MatterService
    from app.services.matters.requests import RequestService

    matter = await MatterService(db).get(matter_id)
    if matter is None:
        return {}

    requests = RequestService(db)
    facts = await FactService(db).find(matter_id=matter_id)
    answers: list[dict[str, Any]] = []
    due: Any = None

    # The whole matter in a handful of queries, never one per row: a
    # renewal with four letters and twenty asks was walked request by
    # request, item by item, link by link, and then document by document
    # for the sources (2026-09-18).
    letters = await requests.for_matter(matter_id)
    items_of = await requests.items_of([one.id for one in letters])
    links_of = await satisfied_by_many(
        db, [item.id for items in items_of.values() for item in items]
    )
    for request in letters:
        # The nearest deadline governs the sheet: a person holding it
        # wants the date they are working to, not a list of them.
        if request.due_on and (due is None or request.due_on < due):
            due = request.due_on
        for item in items_of.get(request.id, []):
            links = links_of.get(item.id, [])
            state = _state(item.status, bool(links))
            answers.append(
                {
                    "item_id": item.id,
                    "asked": item.asked,
                    "kind": item.kind,
                    "state": state,
                    "as_of": item.as_of,
                    "value": _figure(item, facts, links),
                    "sources": await _sources(db, links),
                    # What the register says, when nothing proves it yet.
                    # Offered, never counted: an item with only this
                    # against it stays missing, because a number our own
                    # ledger believes is not a number a statement proves.
                    "unproven": (
                        await unproven_figures(db, matter_id, item)
                        if state == MISSING
                        else []
                    ),
                    "resolution": item.resolution,
                }
            )

    return {
        "matter": matter,
        "reference": matter.reference,
        "due_on": due,
        "answers": answers,
        "outstanding": sum(1 for one in answers if one["state"] == MISSING),
    }


def _figure(item: Any, facts: list[Any], links: list[Any]) -> str | None:
    """The figure that answers this ask, said the way a form wants it.

    A figure prints against an ask only when something TIES the two: it
    is filed as that ask's evidence, or it was read off the paper that
    is. The county asked for gross monthly income twice - once for a
    pension, once for Social Security - and both mean gross_income, so
    matching on the attribute alone printed the pension's figure against
    the Social Security ask. A wrong number on a benefits form is worse
    than the blank it replaced (2026-09-18).

    A figure recorded but filed against nothing prints nowhere. That is
    not a gap: the ask is still shown as missing, and where the register
    can speak to it, ``unproven_figures`` says what it says and marks it
    unverified. Both are better than a number sitting under a question
    nobody joined it to.

    Two tied figures that say the same thing are one answer; two that
    disagree are a question for a person, not a number for a form.
    """
    from app.services.matters.facts import LABELS, said_value

    if not item.ask:
        return None
    candidates = [
        fact
        for fact in facts
        if fact.attribute == item.ask and fact.value_cents is not None
    ]
    filed = {link.fact_id for link in links if link.fact_id}
    papers = {link.document_id for link in links if link.document_id}
    tied = [
        fact
        for fact in candidates
        if fact.id in filed or (fact.document_id and fact.document_id in papers)
    ]
    said = {(fact.value_cents, fact.period) for fact in tied}
    if len(said) != 1:
        return None
    value_cents, period = said.pop()
    return f"{said_value(value_cents, period)} ({LABELS.get(item.ask, item.ask)})"


async def _sources(db: AsyncSession, links: list[Any]) -> list[dict[str, Any]]:
    """Where each answer came from, by NAME and page.

    A document id on a printed sheet is a number nobody can follow. The
    title and the page are what somebody holds the sheet up against.
    """
    from app.services.documents.service import DocumentService

    wanted = [link.document_id for link in links if link.document_id]
    if not wanted:
        return []
    documents = await DocumentService(db).get_many(wanted)
    return [
        {
            "document_id": link.document_id,
            "title": documents[link.document_id].title,
            "page": link.page,
            "note": link.note,
        }
        for link in links
        if link.document_id and link.document_id in documents
    ]
