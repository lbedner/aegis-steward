"""The Mail section: what came in, and what each message became.

The approvals queue says what is WAITING; this says what ARRIVED. The
rows are ``arrivals()``, the same ones the assistant reads, grouped by
the day they landed.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import render
from app.core.db import get_async_session
from app.services.finance.deps import get_owner_user_id
from app.services.mail.arrivals import Arrival, Day, arrivals

SECTION = section("mail")
router = APIRouter(prefix=SECTION.path)
DOCUMENTS = section("documents").path

COLUMNS = (
    {"key": "sender", "label": "From", "kind": "contact"},
    {"key": "subject", "label": "Subject", "kind": "open"},
    {"key": "attachments", "label": "Attachments", "kind": "open"},
    {"key": "became", "label": "Became"},
)


def _row(arrival: Arrival) -> dict[str, Any]:
    """One message as the table draws it. A known sender is its contact;
    a stranger is plain text. A subject is the letter's door when one
    was filed, else plain text."""
    subject: Any = arrival.subject
    if arrival.letter_id is not None:
        subject = {
            "label": arrival.subject,
            "url": f"{DOCUMENTS}/{arrival.letter_id}",
        }
    return {
        "id": arrival.id,
        "sender": {"id": arrival.party["id"], "label": arrival.party["name"]}
        if arrival.party
        else arrival.sender,
        "subject": subject,
        "attachments": [
            {"label": a.filename, "url": f"{DOCUMENTS}/{a.document_id}"}
            for a in arrival.attachments
        ]
        or "",
        "became": arrival.became,
    }


def _day(day: Day) -> dict[str, Any]:
    return {"date": day.date, "rows": [_row(m) for m in day.messages]}


@router.get("", include_in_schema=False)
async def page(
    request: Request, owner_user_id: int | None = Depends(get_owner_user_id)
) -> Response:
    async with get_async_session() as db:
        days = await arrivals(db, owner_user_id=owner_user_id)
    return render(
        request,
        "pages/mail.html",
        {
            "section": SECTION,
            "path": SECTION.path,
            "upload": f"{DOCUMENTS}/new",
            "columns": list(COLUMNS),
            "days": [_day(d) for d in days],
        },
    )
