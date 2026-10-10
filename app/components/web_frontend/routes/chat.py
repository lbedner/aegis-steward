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
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from starlette.responses import JSONResponse, Response

from app.components.backend.api.ai.router import ai_service, sync_active_model
from app.components.backend.api.llm.routes import (
    vendor_icon_urls,
)
from app.components.web_frontend.chat_markers import components
from app.components.web_frontend.filters import assistant
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import (
    CHAT_URLS,
    close_dialog,
    dialog,
    or_404,
    render,
    templates,
)
from app.core.chat_transcript import (
    IMAGE_TYPES,
    MAX_IMAGE_BYTES,
    footer_line,
    readable,
    strip_paste_markers,
    trace_failed,
    trace_label,
    trace_output,
)
from app.core.storage import get_storage, validate_key
from app.services.ai.domains.chat.agent_loader import DEFAULT_AGENT_SLUG
from app.services.ai.domains.chat.attachments import lift_pastes
from app.services.ai.domains.chat.cards import MARKER
from app.services.ai.domains.chat.pastes import PASTE_THRESHOLD
from app.services.ai.domains.llm.picker import (
    is_local_model,
)
from app.services.ai.service.trace import BATCH_MARKER, CHANGE_MARKER
from app.services.finance.domains.detection.analyst.shared import STANDALONE_USER_ID

SECTION = section("chat")
router = APIRouter()

# The agent row the page embeds (persona, tools, sampling and code mode
# all come from the DB), the history scope it lists under, and the name
# the transcript gives the assistant.
AGENT_SLUG = "finance-assistant"
SURFACE = "finance"
ASSISTANT_NAME = assistant(AGENT_SLUG)
STREAM_URL = "/api/v1/ai/chat/stream"

# Where the chat is mounted -> the agent she runs as there, each with its
# own history. Illiana on every one; in the Overseer, the default agent,
# whom the stream gives the system's context (health, usage, catalog).
SURFACES: dict[str, str] = {SURFACE: AGENT_SLUG, "overseer": DEFAULT_AGENT_SLUG}


def turn_defaults(surface: str = SURFACE) -> dict[str, Any]:
    """What the client posts to start a turn. The finance owner id, not
    "api-user", which the snapshot-memory module cannot parse."""
    user = STANDALONE_USER_ID
    return {"user_id": user, "agent_slug": SURFACES[surface], "surface": surface}


def known(surface: str) -> str:
    """A surface named in a request, or a 404."""
    return or_404(surface if surface in SURFACES else None)


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
            "url": f"{MESSAGES}/{conversation_id}/{message_id}/runs/{i}",
        }
        for i, e in enumerate(entries)
    ]


# --- generative components --------------------------------------------------
# What a message's trace draws is read in ``chat_markers.components``; the
# cards themselves load from these routes (and ``chat_cards`` for drawn ones).

COMPONENTS = SECTION.path + "/components"
# Where each kind of component loads from, by its marker's kind: the one
# home for the routes (chat_changes, chat_cards) and the markup that loads
# and decides them (``component_url`` in templates).
COMPONENT_PATHS = {
    CHANGE_MARKER: COMPONENTS + "/change",
    BATCH_MARKER: COMPONENTS + "/batch",
    MARKER: COMPONENTS + "/card",
}


def component_url(kind: str, component_id: object) -> str | None:
    """A component's address; None for a kind nobody draws."""
    path = COMPONENT_PATHS.get(kind)
    return f"{path}/{component_id}" if path else None


templates.env.globals["component_url"] = component_url

ATTACHMENTS = SECTION.path + "/attachments"
PASTES = SECTION.path + "/pastes"
SPEECH = SECTION.path + "/speech"  # routes/chat_speech.py
TURNS = SECTION.path + "/turns"
CONVERSATIONS = SECTION.path + "/conversations"
MESSAGES = SECTION.path + "/messages"
CHAT_URLS.update(
    turns=TURNS, conversations=CONVERSATIONS, messages=MESSAGES, pastes=PASTES
)
# What the composer takes, read by its file input and its script alike:
# the images a model sees, a PDF the documents service reads, and the
# length past which a paste is a document rather than a message.
templates.env.globals["chat_composer"] = {
    "images": sorted(IMAGE_TYPES),
    "documents": ["application/pdf"],
    "paste_threshold": PASTE_THRESHOLD,
    "max_bytes": MAX_IMAGE_BYTES,
}


def paste_chip(paste: dict[str, Any]) -> dict[str, Any]:
    """One stored paste, as the chip that stands in for it."""
    return {
        "id": paste["id"],
        "title": paste.get("title") or "pasted text",
        "chars": paste.get("chars") or 0,
        "url": f"{PASTES}/{paste['id']}",
    }


def attachment_url(stored: dict[str, Any]) -> str:
    """Where a stored image is served from (see ``attachment``)."""
    return f"{ATTACHMENTS}/{stored['key']}?" + urlencode(
        {"type": stored.get("media_type") or "image/png"}
    )


async def model_icons(messages: list[Any]) -> dict[str, str]:
    """``{provider: icon URL}`` for the providers that answered these
    messages, from the same icon store the picker's vendors use. A URL,
    not the bytes: a long thread inlined one logo hundreds of times."""
    providers = sorted(
        {
            str((m.metadata or {}).get("provider"))
            for m in messages
            if (m.metadata or {}).get("provider")
        }
    )
    return await vendor_icon_urls(providers) if providers else {}


def settled(
    message: Any, conversation_id: str, icons: dict[str, str] | None = None
) -> dict[str, Any]:
    """One stored message, shaped for the bubble macro."""
    meta = message.metadata or {}
    trace = meta.get("tool_trace") or []
    return {
        "model_icon": (icons or {}).get(str(meta.get("provider") or "")),
        # The images a user message carried, served by key so a reopened
        # conversation still shows what was pasted.
        "attachments": [
            {"url": attachment_url(a), "name": a.get("name") or "image"}
            for a in meta.get("attachments") or []
            if a.get("key")
        ],
        "id": message.id,
        "role": message.role.value,
        "pastes": [paste_chip(p) for p in meta.get("pastes") or []],
        "content": readable(message.content),
        "at": message.timestamp.isoformat(),
        "trail": trail(trace, conversation_id, message.id),
        "components": components(trace),
        "footer": footer_line(meta, local=is_local_model(meta)),
        "speech": f"{SPEECH}/{conversation_id}/{message.id}",
    }


async def owned(conversation_id: str) -> Any:
    """This surface's conversation, or a 404: another user's is as absent
    as a missing one."""
    conversation = await ai_service.get_conversation(conversation_id)
    if conversation is None or conversation.user_id != STANDALONE_USER_ID:
        raise HTTPException(status_code=404)
    return conversation


async def stored_message(conversation_id: str, message_id: str) -> Any:
    conversation = await owned(conversation_id)
    found = next((m for m in conversation.messages if m.id == message_id), None)
    or_404(found)
    return found


HISTORY_LIMIT = 25


async def _conversations(surface: str = SURFACE) -> list[Any]:
    """This surface's conversations, newest first."""
    return await ai_service.list_conversations(STANDALONE_USER_ID, surface=surface)


# A thread opens on its latest messages, and earlier ones come a page at a
# time from a row at the top: rendering all of a long one made the chat
# page megabytes (493 messages, 2.6 MB, 2026-09-28).
THREAD_PAGE = 40


def earlier_url(conversation_id: str, before: str) -> str:
    return f"{CONVERSATIONS}/{conversation_id}/earlier?before={before}"


async def _thread(
    request: Request, conversation: Any | None, before: str | None = None, **how: bool
) -> Response:
    """The transcript partial: a conversation's thread (a page of it before
    ``before``), swapped in as ``how`` says (``oob``, ``page``)."""
    return templates.TemplateResponse(
        request=request,
        name="partials/chat/transcript.html",
        context={
            "assistant": ASSISTANT_NAME,
            **how,
            **await _transcript(conversation, before),
        },
    )


async def _transcript(
    conversation: Any | None, before: str | None = None
) -> dict[str, Any]:
    """A page of the thread: the latest messages, or those just before
    ``before`` (a message id); ``earlier`` is where the page before it
    loads from, when there is one."""
    if conversation is None:
        return {"conversation_id": None, "messages": []}
    messages = conversation.messages
    if before:
        ids = [m.id for m in messages]
        messages = messages[: ids.index(before)] if before in ids else []
    page = messages[-THREAD_PAGE:]
    icons = await model_icons(page)
    return {
        "conversation_id": conversation.id,
        "messages": [settled(m, conversation.id, icons) for m in page],
        "earlier": earlier_url(conversation.id, page[0].id)
        if len(messages) > len(page)
        else None,
    }


async def surface_context(surface: str = SURFACE) -> dict[str, Any]:
    """Everything the chat surface renders from, for the page and the
    drawer alike: it opens onto the most recent conversation, never
    blank unless there is none."""
    latest = next(iter(await _conversations(surface)), None)
    return {
        "assistant": ASSISTANT_NAME,
        "path": SECTION.path,
        "surface": surface,
        "stream_url": STREAM_URL,
        "turn_defaults": turn_defaults(surface),
        **await _transcript(latest),
    }


@router.get(SECTION.path, include_in_schema=False)
async def page(request: Request, _: None = Depends(sync_active_model)) -> Response:
    return render(
        request, "pages/chat.html", {"section": SECTION, **await surface_context()}
    )


@router.get(SECTION.path + "/drawer", include_in_schema=False)
async def drawer(
    request: Request, surface: str = SURFACE, _: None = Depends(sync_active_model)
) -> Response:
    """The surface for the Illiana drawer: the same partial the page
    includes, from the same context."""
    return templates.TemplateResponse(
        request=request,
        name="partials/chat/surface.html",
        context=await surface_context(known(surface)),
    )


@router.get(CONVERSATIONS, include_in_schema=False)
async def history(request: Request, surface: str = SURFACE) -> Response:
    return dialog(
        request,
        "partials/chat/history.html",
        conversations=(await _conversations(known(surface)))[:HISTORY_LIMIT],
        path=SECTION.path,
    )


@router.get(CONVERSATIONS + "/new", include_in_schema=False)
async def new_conversation(request: Request) -> Response:
    """An empty thread; the id arrives on the first turn's first frame."""
    return await _thread(request, None, oob=True)


@router.get(CONVERSATIONS + "/{conversation_id}", include_in_schema=False)
async def load_conversation(request: Request, conversation_id: str) -> Response:
    """The thread for a conversation picked from history, replacing the
    current one in place; the dialog closes on the way."""
    conversation = await owned(conversation_id)
    return close_dialog(await _thread(request, conversation, oob=True))


@router.get(
    CONVERSATIONS + "/{conversation_id}/earlier",
    include_in_schema=False,
)
async def earlier(request: Request, conversation_id: str, before: str) -> Response:
    """The page of the thread before ``before``, in place of the row that
    asked for it (and the next such row, when there is more)."""
    return await _thread(request, await owned(conversation_id), before, page=True)


@router.post(TURNS, include_in_schema=False)
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
    # A wall of pasted text is lifted HERE as well as in the service, so
    # it never lands on screen as a wall and the text the script posts
    # on already carries its markers. Storing is content-addressed and
    # the index de-duplicates, so the service lifting the same message
    # again costs nothing and lands on the same ids.
    text, pastes = await lift_pastes(text, str(STANDALONE_USER_ID))
    return templates.TemplateResponse(
        request=request,
        name="partials/chat/turn.html",
        context={
            "assistant": ASSISTANT_NAME,
            # The reader sees the chip; the MODEL sees the marker, which
            # rides ``data-text`` on the bubble the script posts from.
            "text": strip_paste_markers(text),
            "sent": text,
            "pastes": [paste_chip(p) for p in pastes.get("pastes") or []],
            "conversation_id": conversation_id or None,
            "attachment_names": [n for n in attachment_names if n],
        },
    )


@router.post(PASTES, include_in_schema=False)
async def stage_paste(text: Annotated[str, Form()] = "") -> Response:
    """Store a wall of text the moment it is pasted, and answer with the
    marker that stands in for it.

    The BROWSER is the only thing that knows a paste was a paste. A
    server looking at finished message text can only guess from its
    shape, and that guess is wrong on the documents people actually
    paste: an order page is thousands of single-newline lines, so
    splitting on blank lines lifts the few paragraphs that happen to be
    long and leaves the page behind. So the composer says so at paste
    time, and everything downstream sees a message that already carries
    its marker.
    """
    from app.services.ai.domains.chat.pastes import marker, store_paste, title_of

    if not text.strip():
        return Response(status_code=422)
    paste = await store_paste(str(STANDALONE_USER_ID), text, title=title_of(text))
    return JSONResponse({"marker": marker(paste), **paste_chip(paste)})


@router.get(PASTES + "/{paste_id}", include_in_schema=False)
async def paste(request: Request, paste_id: str) -> Response:
    """A pasted block, in the one modal. The text is shown as text -
    monospaced and scrolling - because what was pasted was a page, and
    rendering it as markdown would reflow the thing the reader came to
    check."""
    from app.services.ai.domains.chat.pastes import read_paste

    found = await read_paste(str(STANDALONE_USER_ID), paste_id)
    or_404(found)
    return templates.TemplateResponse(
        request=request,
        name="partials/chat/paste.html",
        context={"paste": paste_chip(found[0]), "text": found[1]},
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
    or_404(data)
    return Response(
        content=data,
        media_type=type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get(MESSAGES + "/{conversation_id}/{message_id}", include_in_schema=False)
async def message(request: Request, conversation_id: str, message_id: str) -> Response:
    """The settled bubble for a stored message, swapped over the streaming
    one once the turn completes (the message is persisted before the
    stream's final frame)."""
    found = await stored_message(conversation_id, message_id)
    return templates.TemplateResponse(
        request=request,
        name="partials/chat/message.html",
        context={
            "assistant": ASSISTANT_NAME,
            "message": settled(found, conversation_id, await model_icons([found])),
        },
    )


@router.get(
    MESSAGES + "/{conversation_id}/{message_id}/runs/{index}",
    include_in_schema=False,
)
async def run_detail(
    request: Request, conversation_id: str, message_id: str, index: int
) -> Response:
    """What one tool run actually did, in the one modal: the script (or
    the arguments), its output, and the calls it dispatched."""
    entries = ((await stored_message(conversation_id, message_id)).metadata or {}).get(
        "tool_trace"
    ) or []
    if not 0 <= index < len(entries):
        raise HTTPException(status_code=404)
    return dialog(request, "partials/chat/run.html", run=run(entries[index]))
