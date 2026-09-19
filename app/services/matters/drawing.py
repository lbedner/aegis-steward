"""How a request DRAWS: the shapes a page reads, not the rows it keeps.

Split from ``requests`` at the budget, and the seam is a real one. What
a request IS - recorded, amended, marked, settled - is the service; this
is the same rows arranged for somebody to look at: the steps of a
letter, the alternatives folded into one line each, the papers filed
against them.

Nothing here writes. A page that re-derives a status while drawing it is
a page that will disagree with the row it drew.
"""

from __future__ import annotations

from datetime import date
import re
from typing import Any

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.matters.models import ITEM_KINDS, Request, RequestItem
from app.services.matters.requests import SETTLED, RequestService, overdue, standing


async def drawn(
    db: AsyncSession, request: Request, today: date | None = None
) -> dict[str, Any]:
    """One request as a page draws it.

    The status and the lateness are computed HERE, once, so the template
    never has to decide what "satisfied" means and no second copy of the
    rule can drift from this one.
    """
    items = await RequestService(db).items(request.id)
    settled, total = standing(items)
    papers = await titles(db, [item.document_id for item in items])
    late = overdue(request, today)
    letter = (await titles(db, [request.document_id])).get(request.document_id or 0)
    return {
        "id": request.id,
        "matter_id": request.matter_id,
        # The page it all came from, read beside the asks it produced.
        "document_id": request.document_id,
        "letter": letter,
        "received_on": request.received_on,
        "due_on": request.due_on,
        "status": request.status,
        "overdue": late,
        "tone": "error" if late else ("ok" if request.status != "open" else "muted"),
        "settled": settled,
        "total": total,
        "note": request.note,
        # Grouped for the page: alternatives are ONE step carrying its
        # options, because the county will take any one of them. Not
        # "items" as a key either way - Jinja resolves ``thing.items``
        # to the dict method before the key, and the loop walks a
        # builtin instead.
        "steps": steps(items, papers),
    }


_BULLET = re.compile(r"^(?:[-*\u2022]|\d{1,2}[.)])\s*")


async def titles(
    db: AsyncSession, document_ids: list[int | None]
) -> dict[int, dict[str, Any]]:
    """The attached documents as a page names them, in ONE query.

    A page draws a dozen items and a title per item is a dozen round
    trips, which is the N+1 the house rules name outright.
    """
    from app.services.documents.models import Document

    wanted = [one for one in document_ids if one]
    if not wanted:
        return {}
    rows = (await db.exec(select(Document).where(col(Document.id).in_(wanted)))).all()
    return {
        row.id: {"id": row.id, "title": row.title, "media_type": row.media_type}
        for row in rows
        if row.id is not None
    }


def steps(
    items: list[RequestItem], papers: dict[int, dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """The asks as work: one step per demand, alternatives inside it.

    The step takes its wording from the first option and its state from
    the group - settled when ANY option is, because that is what "we
    will take any one of these" means.
    """
    order: list[str] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        key = item.option_group or f"item:{item.id}"
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(_option(item, papers or {}))
    out = []
    for key in order:
        options = grouped[key]
        lead = options[0]
        out.append(
            {
                "id": key,
                "asked": lead["asked"],
                "kind": lead["kind"],
                "kind_label": lead["kind_label"],
                "as_of": lead["as_of"],
                "settled": any(option["settled"] for option in options),
                "options": options,
            }
        )
    return out


def _option(item: RequestItem, papers: dict[int, dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": item.id,
        "ordinal": item.ordinal,
        "asked": item.asked,
        "kind": item.kind,
        "kind_label": dict(ITEM_KINDS).get(item.kind, item.kind),
        "ask": item.ask,
        "as_of": item.as_of,
        "status": item.status,
        "settled": item.status in SETTLED,
        "resolution": item.resolution,
        "document": papers.get(item.document_id or 0),
    }


def asked_lines(text: str) -> list[dict[str, Any]]:
    """A letter's demands as somebody types them: one ask per line.

    Blank lines and a leading bullet or number are dropped - people
    paste from the letter, and the letter numbers its own list.
    """
    items: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        cleaned = _BULLET.sub("", line.strip()).strip()
        if cleaned:
            items.append({"asked": cleaned})
    return items
