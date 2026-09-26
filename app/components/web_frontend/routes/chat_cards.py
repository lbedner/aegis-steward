"""The cards Illiana draws in a reply (#266), one route.

A drawn card rides her answer's trace as a ``chat_card`` marker; the
settled message places a loader for it (the ``component`` macro), and this
draws the kind's template from the card's frozen payload. A card that is
gone, whose kind is gone, or that belongs to another conversation says so
quietly: 200, never 404, because a transcript is a record.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError
from starlette.responses import Response

from app.components.web_frontend.rendering import dialog, render
from app.components.web_frontend.routes.chat import COMPONENTS, owned
from app.services.ai.domains.chat import cards

router = APIRouter()

CARDS = COMPONENTS + "/card"
MISSING = "partials/chat/card_missing.html"


@router.get(CARDS + "/{card_id}", include_in_schema=False)
async def card(request: Request, card_id: str, size: str = "") -> Response:
    row = await cards.stored_card(card_id)
    kind = cards.lookup(row.kind) if row else None
    if row is None or kind is None:
        return render(request, MISSING)
    try:
        await owned(row.conversation_id)
        # Read through the kind's schema: its defaults fill in, and a
        # payload an old version stored in another shape is missing, not
        # a template error.
        payload = kind.schema.model_validate(row.payload).model_dump()
    except (HTTPException, ValidationError):
        return render(request, MISSING)
    context = {
        "card": payload,
        "card_id": row.id,
        "card_url": f"{CARDS}/{row.id}",
        "large": size == "large",
    }
    if context["large"]:
        return dialog(request, kind.template, **context)
    return render(request, kind.template, context)
