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

# The three states an ask can be in on the sheet. "waived" and
# "not_applicable" are SETTLED, not missing: somebody said the county
# did not ask for this, and printing it as outstanding sends them
# chasing paper nobody wants.
ANSWERED = "answered"
MISSING = "missing"
WAIVED = "waived"


def _state(status: str, has_evidence: bool) -> str:
    if status in ("waived", "not_applicable"):
        return WAIVED
    return ANSWERED if has_evidence or status == "satisfied" else MISSING


async def answer_sheet(db: AsyncSession, matter_id: int) -> dict[str, Any]:
    """The whole sheet for one matter.

    Returns the matter's own identifiers - a sheet beside a form needs
    the case number the county files it under, or it is a page about
    nothing in particular - the asks in the order they were asked, and
    a count of what is still outstanding.
    """
    from app.services.finance.domains.detection.insights.formatting import format_usd
    from app.services.matters.evidence import satisfied_by
    from app.services.matters.facts import LABELS, FactService
    from app.services.matters.matters import MatterService
    from app.services.matters.requests import RequestService

    matter = await MatterService(db).get(matter_id)
    if matter is None:
        return {}

    requests = RequestService(db)
    facts = await FactService(db).find(matter_id=matter_id)
    answers: list[dict[str, Any]] = []
    due: Any = None

    for request in await requests.for_matter(matter_id):
        # The nearest deadline governs the sheet: a person holding it
        # wants the date they are working to, not a list of them.
        if request.due_on and (due is None or request.due_on < due):
            due = request.due_on
        for item in await requests.items(request.id):
            links = await satisfied_by(db, item.id)
            state = _state(item.status, bool(links))
            answers.append(
                {
                    "item_id": item.id,
                    "asked": item.asked,
                    "kind": item.kind,
                    "state": state,
                    "as_of": item.as_of,
                    "value": _figure(item, facts, format_usd, LABELS),
                    "sources": await _sources(db, links),
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


def _figure(
    item: Any, facts: list[Any], money: Any, labels: dict[str, str]
) -> str | None:
    """The figure that answers this ask, said the way a form wants it.

    Matched on the ask's own ``ask`` key where it has one, because an
    item that names an attribute is an item somebody already decided the
    meaning of. Nothing is guessed from the sentence: a figure put
    against the wrong question is worse on a form than a blank.
    """
    if not item.ask:
        return None
    for fact in facts:
        if fact.attribute != item.ask or fact.value_cents is None:
            continue
        said = money(fact.value_cents)
        if fact.period and fact.period != "once":
            said = f"{said} a {fact.period}"
        return f"{said} ({labels.get(fact.attribute, fact.attribute)})"
    return None


async def _sources(db: AsyncSession, links: list[Any]) -> list[dict[str, Any]]:
    """Where each answer came from, by NAME and page.

    A document id on a printed sheet is a number nobody can follow. The
    title and the page are what somebody holds the sheet up against.
    """
    from app.services.documents.service import DocumentService

    documents = DocumentService(db)
    said: list[dict[str, Any]] = []
    for one in links:
        if one.document_id is None:
            continue
        document = await documents.get(one.document_id)
        if document is None:
            continue
        said.append(
            {
                "document_id": one.document_id,
                "title": document.title,
                "page": one.page,
                "note": one.note,
            }
        )
    return said
