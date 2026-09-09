"""The Chat section: a conversation with Illiana, rendered.

API-only by design, like the Flet panel it replaces: the turn itself
streams from ``POST /api/v1/ai/chat/stream`` straight to the browser,
and everything the page shows around it is server HTML. A turn starts
here (``POST /chat/turns`` answers the user bubble and an empty assistant
bubble the script streams into) and settles here (``GET
/chat/messages/{conversation}/{message}`` renders the stored message:
markdown, the tool trail, its components and the footer). The client
never renders a settled message, so history replay and a live turn
share one macro.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from starlette.responses import Response

from app.components.backend.api.ai.router import ai_service, sync_active_model
from app.components.backend.api.finance.changes import (
    approve_batch,
    approve_change,
    get_batch,
    get_change,
    reject_batch,
    reject_change,
)
from app.components.web_frontend.filters import assistant
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import (
    close_dialog,
    dialog,
    render,
    templates,
)
from app.core.storage import get_storage, validate_key
from app.services.ai.domains.chat.transcript import (
    footer_line,
    strip_attachment_marker,
    trace_failed,
    trace_label,
    trace_output,
)
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.domains.detection.analyst.shared import STANDALONE_USER_ID
from app.services.finance.schemas.changes import (
    BatchResolveRequest,
    PendingChangeResponse,
)
from app.services.finance.service import FinanceService

SECTION = section("chat")
router = APIRouter()

# The agent row the page embeds (persona, tools, sampling and code mode
# all come from the DB), the history scope it lists under, and the name
# the transcript gives the assistant.
AGENT_SLUG = "finance-assistant"
SURFACE = "finance"
ASSISTANT_NAME = assistant(AGENT_SLUG)
STREAM_URL = "/api/v1/ai/chat/stream"

# What the client posts to start a turn, ready for the stream endpoint.
# The finance owner id, not the generic one: the snapshot-memory module
# cannot parse "api-user" and would silently skip the briefing.
TURN_DEFAULTS: dict[str, Any] = {
    "user_id": STANDALONE_USER_ID,
    "agent_slug": AGENT_SLUG,
    "surface": SURFACE,
}


def run(entry: dict[str, Any]) -> dict[str, Any]:
    """One tool run, shaped for the run dialog: its script or arguments,
    its output, and the calls a script dispatched."""
    code = entry.get("code") if isinstance(entry.get("code"), str) else None
    return {
        "tool": str(entry.get("tool") or "tool call"),
        "failed": trace_failed(entry),
        "code": code,
        "args": pretty_json(entry.get("args")) if not code else None,
        "output": trace_output(entry),
        "nested": entry.get("nested") or [],
    }


def pretty_json(text: Any) -> str | None:
    """A tool's arguments as the reader wants them: the JSON re-indented
    when it parses, the raw string when it does not."""
    if not isinstance(text, str) or not text:
        return None
    try:
        return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except ValueError:
        return text


def trail(
    entries: list[dict[str, Any]], conversation_id: str, message_id: str
) -> list[dict[str, Any]]:
    """A stored tool trace as the trail's rows: a label, the failed tone,
    and the dialog that shows what the run actually did."""
    return [
        {
            "label": trace_label(e),
            "failed": trace_failed(e),
            "url": f"{SECTION.path}/messages/{conversation_id}/{message_id}/runs/{i}",
        }
        for i, e in enumerate(entries)
    ]


# --- generative components --------------------------------------------------
#
# A tool result carries DATA; presentation is system code selected by kind
# from the registry (the ``component`` macro); the model never authors
# layout, and an unknown kind degrades to absence. A card carries identity
# only and loads its rows from the queue, so it always shows the queue's
# truth: the result blob in the trace is clipped, and a resolution made on
# the Review page must reach a card already in the transcript.

COMPONENTS = SECTION.path + "/components"
CARD_TOOLS = ("propose", "propose_many", "pending")


def components(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``{kind, id}`` for every card the trace carries: its compact
    markers (one per card), else the parsed result of a pre-marker
    trace, else the identity salvaged from a clipped one."""
    found: list[dict[str, Any]] = []
    for entry in trace:
        if entry.get("tool") not in CARD_TOOLS:
            continue
        for data in _card_data(entry):
            if data.get("batch_id"):
                found.append({"kind": "pending_change_batch", "id": data["batch_id"]})
            elif data.get("pending_change_id"):
                found.append(
                    {"kind": "pending_change", "id": data["pending_change_id"]}
                )
    return found


def _card_data(entry: dict[str, Any]) -> list[dict[str, Any]]:
    marker = entry.get("component")
    markers = marker if isinstance(marker, list) else [marker]
    found = [m for m in markers if isinstance(m, dict) and m.get("kind")]
    if found or entry.get("tool") == "pending":
        return found
    result = entry.get("result")
    if not isinstance(result, str):
        return []
    try:
        parsed = json.loads(result)
    except (TypeError, ValueError):
        parsed = _salvage_identity(result)
    return [parsed] if isinstance(parsed, dict) else []


def _salvage_identity(clipped: str) -> dict[str, Any] | None:
    """Identity fields lead a proposal's result, so they survive the
    display clip that truncates the rest to invalid JSON."""
    fields: dict[str, Any] = {}
    for key in ("batch_id", "pending_change_id"):
        m = re.search(rf'"{key}":\s*"?([^",}}]*)"?', clipped)
        if m:
            fields[key] = m.group(1)
    return fields or None


STATUS_COPY = {
    "pending": ("Awaiting your approval", "warn"),
    "approved": ("Approved", "ok"),
    "rejected": ("Rejected", "error"),
    "withdrawn": ("Withdrawn", "muted"),
    "expired": ("Expired", "muted"),
}


def status_of(change: PendingChangeResponse) -> tuple[str, str, str | None]:
    """The status word to show, its tone, and a note under it. A
    withdrawal lands in the queue as a rejection with a note, so the
    audit trail stays one shape; on the card it is the assistant taking
    its own proposal back, and its reason is the line worth reading."""
    status = change.status
    note = change.note
    if status == "rejected" and note and note.startswith("Withdrawn"):
        status = "withdrawn"
    else:
        note = None
    label, tone = STATUS_COPY.get(status, (status.title(), "muted"))
    return label, tone, note


def change_card(change: PendingChangeResponse) -> dict[str, Any]:
    label, tone, note = status_of(change)
    return {
        "change": change,
        "status": label,
        "tone": tone,
        "note": note,
        "pending": change.status == "pending",
    }


def batch_card(batch_id: str, items: list[PendingChangeResponse]) -> dict[str, Any]:
    rows = [change_card(c) for c in items]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"].lower()] = counts.get(row["status"].lower(), 0) + 1
    return {
        "batch_id": batch_id,
        "title": items[0].title if items else "",
        "rows": rows,
        "pending": any(r["pending"] for r in rows),
        "outcome": ", ".join(f"{n} {word}" for word, n in sorted(counts.items())),
    }


ATTACHMENTS = SECTION.path + "/attachments"


def attachment_url(stored: dict[str, Any]) -> str:
    """Where a stored image is served from (see ``attachment``)."""
    from urllib.parse import urlencode

    return f"{ATTACHMENTS}/{stored['key']}?" + urlencode(
        {"type": stored.get("media_type") or "image/png"}
    )


def settled(message: Any, conversation_id: str) -> dict[str, Any]:
    """One stored message, shaped for the bubble macro."""
    meta = message.metadata or {}
    trace = meta.get("tool_trace") or []
    return {
        # The images a user message carried, served by key so a reopened
        # conversation still shows what was pasted.
        "attachments": [
            {"url": attachment_url(a), "name": a.get("name") or "image"}
            for a in meta.get("attachments") or []
            if a.get("key")
        ],
        "id": message.id,
        "role": message.role.value,
        # A replayed user message must not carry its attachment marker:
        # the bytes rode one turn only, and re-sending the marker would
        # claim images the model cannot see.
        "content": strip_attachment_marker(message.content),
        "at": message.timestamp.isoformat(),
        "trail": trail(trace, conversation_id, message.id),
        "components": components(trace),
        "components_base": COMPONENTS,
        "footer": footer_line(meta),
    }


def _stored(conversation_id: str, message_id: str) -> Any:
    conversation = ai_service.get_conversation(conversation_id)
    if (
        conversation is None
        or conversation.metadata.get("user_id") != STANDALONE_USER_ID
    ):
        raise HTTPException(status_code=404)
    found = next((m for m in conversation.messages if m.id == message_id), None)
    if found is None:
        raise HTTPException(status_code=404)
    return found


HISTORY_LIMIT = 25


def _conversations() -> list[Any]:
    """This surface's conversations, newest first."""
    return ai_service.list_conversations(STANDALONE_USER_ID, surface=SURFACE)


def _transcript(conversation: Any | None) -> dict[str, Any]:
    if conversation is None:
        return {"conversation_id": None, "messages": []}
    return {
        "conversation_id": conversation.id,
        "messages": [settled(m, conversation.id) for m in conversation.messages],
    }


@router.get(SECTION.path, include_in_schema=False)
async def page(request: Request, _: None = Depends(sync_active_model)) -> Response:
    """The page opens onto the most recent conversation, never blank
    unless there is none."""
    latest = next(iter(_conversations()), None)
    return render(
        request,
        "pages/chat.html",
        {
            "section": SECTION,
            "assistant": ASSISTANT_NAME,
            "stream_url": STREAM_URL,
            "turn_defaults": TURN_DEFAULTS,
            **_transcript(latest),
        },
    )


@router.get(SECTION.path + "/conversations", include_in_schema=False)
async def history(request: Request) -> Response:
    return dialog(
        request,
        "partials/chat/history.html",
        conversations=_conversations()[:HISTORY_LIMIT],
        path=SECTION.path,
    )


@router.get(SECTION.path + "/conversations/new", include_in_schema=False)
async def new_conversation(request: Request) -> Response:
    """An empty thread; the id arrives on the first turn's first frame."""
    return templates.TemplateResponse(
        request=request,
        name="partials/chat/transcript.html",
        context={"assistant": ASSISTANT_NAME, "oob": True, **_transcript(None)},
    )


@router.get(SECTION.path + "/conversations/{conversation_id}", include_in_schema=False)
async def load_conversation(request: Request, conversation_id: str) -> Response:
    """The thread for a conversation picked from history, replacing the
    current one in place; the dialog closes on the way."""
    conversation = ai_service.get_conversation(conversation_id)
    if (
        conversation is None
        or conversation.metadata.get("user_id") != STANDALONE_USER_ID
    ):
        raise HTTPException(status_code=404)
    response = templates.TemplateResponse(
        request=request,
        name="partials/chat/transcript.html",
        context={"assistant": ASSISTANT_NAME, "oob": True, **_transcript(conversation)},
    )
    return close_dialog(response)


@router.post(SECTION.path + "/turns", include_in_schema=False)
async def start_turn(
    request: Request,
    message: Annotated[str, Form()] = "",
    conversation_id: Annotated[str, Form()] = "",
    attachment_names: Annotated[list[str], Form()] = [],
) -> Response:
    """The user bubble and the assistant bubble the stream lands in. The
    text and the conversation ride on the assistant bubble for the
    script; nothing is sent to the model until the script posts it. The
    images stay in the browser (they ride the stream request, one turn
    only); only their names come here, for the note under the bubble."""
    text = message.strip()
    if not text and attachment_names:
        text = "See the attached images."
    if not text:
        return Response(status_code=422)
    return templates.TemplateResponse(
        request=request,
        name="partials/chat/turn.html",
        context={
            "assistant": ASSISTANT_NAME,
            "text": text,
            "conversation_id": conversation_id or None,
            "attachment_names": [n for n in attachment_names if n],
        },
    )


@router.get(ATTACHMENTS + "/{key:path}", include_in_schema=False)
async def attachment(key: str, type: str = "image/png") -> Response:
    """A stored image by its content key. Content-addressed, so the
    browser may cache it for good."""
    try:
        validate_key(key)
    except ValueError:
        raise HTTPException(status_code=404) from None
    if not type.startswith("image/"):
        raise HTTPException(status_code=404)
    data = await get_storage().get(key)
    if data is None:
        raise HTTPException(status_code=404)
    return Response(
        content=data,
        media_type=type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get(
    SECTION.path + "/messages/{conversation_id}/{message_id}", include_in_schema=False
)
async def message(request: Request, conversation_id: str, message_id: str) -> Response:
    """The settled bubble for a stored message, swapped over the streaming
    one once the turn completes (the message is persisted before the
    stream's final frame)."""
    found = _stored(conversation_id, message_id)
    return templates.TemplateResponse(
        request=request,
        name="partials/chat/message.html",
        context={
            "assistant": ASSISTANT_NAME,
            "message": settled(found, conversation_id),
        },
    )


def _card(request: Request, name: str, card: dict[str, Any]) -> Response:
    return templates.TemplateResponse(
        request=request, name=name, context={"card": card, "base": COMPONENTS}
    )


@router.get(COMPONENTS + "/change/{change_id:int}", include_in_schema=False)
async def change_component(
    request: Request,
    change_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    change = await get_change(change_id, service=service, owner_user_id=owner_user_id)
    return _card(request, "partials/chat/change.html", change_card(change))


@router.post(COMPONENTS + "/change/{change_id:int}/{verb}", include_in_schema=False)
async def resolve_change_component(
    request: Request,
    change_id: int,
    verb: str,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """The card re-renders from whatever the queue says afterwards, never
    an optimistic guess."""
    handler = {"approve": approve_change, "reject": reject_change}.get(verb)
    if handler is None:
        raise HTTPException(status_code=404)
    change = await handler(change_id, service=service, owner_user_id=owner_user_id)
    await service.db.commit()
    return _card(request, "partials/chat/change.html", change_card(change))


@router.get(COMPONENTS + "/batch/{batch_id}", include_in_schema=False)
async def batch_component(
    request: Request,
    batch_id: str,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    listing = await get_batch(batch_id, service=service, owner_user_id=owner_user_id)
    if not listing.items:
        raise HTTPException(status_code=404)
    return _card(
        request, "partials/chat/batch.html", batch_card(batch_id, listing.items)
    )


@router.post(COMPONENTS + "/batch/{batch_id}/{verb}", include_in_schema=False)
async def resolve_batch_component(
    request: Request,
    batch_id: str,
    verb: str,
    exclude_ids: Annotated[list[int], Form()] = [],
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    if verb == "approve":
        await approve_batch(
            batch_id,
            BatchResolveRequest(exclude_ids=exclude_ids),
            service=service,
            owner_user_id=owner_user_id,
        )
    elif verb == "reject":
        await reject_batch(batch_id, service=service, owner_user_id=owner_user_id)
    else:
        raise HTTPException(status_code=404)
    await service.db.commit()
    listing = await get_batch(batch_id, service=service, owner_user_id=owner_user_id)
    return _card(
        request, "partials/chat/batch.html", batch_card(batch_id, listing.items)
    )


@router.get(
    SECTION.path + "/messages/{conversation_id}/{message_id}/runs/{index}",
    include_in_schema=False,
)
async def run_detail(
    request: Request, conversation_id: str, message_id: str, index: int
) -> Response:
    """What one tool run actually did, in the one modal: the script (or
    the arguments), its output, and the calls it dispatched."""
    entries = (_stored(conversation_id, message_id).metadata or {}).get(
        "tool_trace"
    ) or []
    if not 0 <= index < len(entries):
        raise HTTPException(status_code=404)
    return dialog(request, "partials/chat/run.html", run=run(entries[index]))
