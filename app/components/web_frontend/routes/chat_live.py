"""Talking to Illiana live (#252): GPT-Live is her ears and voice, her own
agent does the thinking.

The browser's WebRTC offer comes here and goes on to OpenAI, so the key
never leaves the server; the answer SDP goes back. The session delegates
to the CLIENT: when GPT-Live needs real work, the browser sends what was
said to ``DELEGATIONS``, which runs her voice agent's turn in the
conversation - her prompt, tools and approval cards - and returns what
GPT-Live should say.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from starlette.responses import JSONResponse, Response

from app.components.backend.api.ai.router import ai_service, sync_active_model
from app.components.web_frontend.rendering import templates
from app.components.web_frontend.routes.chat import SECTION, SURFACE, owned
from app.core.chat_transcript import readable
from app.core.config import settings
from app.core.db import get_async_session
from app.core.log import logger
from app.services.ai import usage_recording
from app.services.ai.domains.voice import live_engines, realtime_calls
from app.services.ai.domains.voice.spoken import to_spoken
from app.services.ai.models import AIProvider
from app.services.ai.models.live_engine import LiveEngine
from app.services.finance.domains.detection.analyst.prompts import LIVE_SIGN_OFF
from app.services.finance.domains.detection.analyst.shared import (
    FINANCE_VOICE_AGENT_SLUG,
    STANDALONE_USER_ID,
)

# Her agent answers on the stored active model: a process that reloaded
# mid-call is otherwise on the .env default until a page load syncs it.
router = APIRouter(dependencies=[Depends(sync_active_model)])

LIVE = SECTION.path + "/live"
SESSIONS = LIVE + "/sessions"
DELEGATIONS = LIVE + "/delegations"
USAGE = LIVE + "/usage"

LIVE_FALLBACK_VOICE = "marin"
# A delegation's result is spoken as commentary, capped at 500 tokens.
LIVE_ANSWER_CHARS = 1_500
# The conversation so far, as the session's opening items (128 at most,
# 8,192 tokens): enough to follow on from, short enough to fit.
LIVE_HISTORY = 12
LIVE_HISTORY_CHARS = 500
LIVE_SORRY = "Sorry, I couldn't get to that just now. Can you try again?"
# Handed back without a turn when GPT-Live delegates before any words
# were heard: a stored "(nothing heard)" turn cost a model call to say
# "I'm here" (2026-09-26).
LIVE_NOT_HEARD = "Sorry, I didn't catch that. Could you say it again?"
# Sent the moment the call is up, so she speaks first. Commentary is said
# about 0.6s later; a cue rather than a line, so the greeting varies
# (probed 2026-09-26: "Hi there. What would you like to look at?").
LIVE_GREETING = (
    "The call just connected. Greet them warmly in a few words and ask "
    "what they would like to look at."
)
# What the live button carries for voice.js: the sign-off it hangs up on,
# and what she says when the page cannot get an answer.
templates.env.globals["live_lines"] = {
    "sign_off": LIVE_SIGN_OFF,
    "sorry": LIVE_SORRY,
    "not_heard": LIVE_NOT_HEARD,
    "greeting": LIVE_GREETING,
}


class Offer(BaseModel):
    sdp: str
    conversation_id: str | None = None


class Billed(BaseModel):
    """What OpenAI says the call has billed so far (``session.usage.updated``,
    a running total) and, at the end, why it closed (``session.closed``)."""

    session_id: str
    seconds: float = Field(ge=0)
    reason: str | None = None


class Said(BaseModel):
    text: str
    conversation_id: str | None = None

    @field_validator("text")
    @classmethod
    def _something(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("nothing was said")
        return value.strip()


def _live_api() -> Any:
    from openai import AsyncOpenAI

    return AsyncOpenAI(api_key=settings.OPENAI_API_KEY).live


async def _history(conversation_id: str | None) -> list[dict[str, Any]]:
    if not conversation_id:
        return []
    conversation = await owned(conversation_id)
    items = []
    for message in conversation.messages:
        role = str(getattr(message.role, "value", message.role))
        text = to_spoken(readable(message.content), LIVE_HISTORY_CHARS)
        if role in ("user", "assistant") and text:
            kind = "input_text" if role == "user" else "output_text"
            items.append(
                {
                    "type": "message",
                    "role": role,
                    "content": [{"type": kind, "text": text}],
                }
            )
    return items[-LIVE_HISTORY:]


@router.post(SESSIONS, include_in_schema=False)
async def open_session(offer: Offer) -> Response:
    """The browser's offer, answered by the engine her voice profile names
    (#273): GPT-Live handing real work back to the page, or a realtime
    model running her own agent. The answer says which, so the page
    speaks that engine's events."""
    # Its own short session: this one is BEGIN IMMEDIATE, and the call's
    # ledger row is written in another before the answer goes back.
    async with get_async_session() as db:
        engine = await live_engines.resolve(db, settings.VOICE_LIVE_ENGINE)
        # What the call bar shows: the engine, and its rate when it bills
        # by the second; a token-billed engine is priced after the call.
        info = engine and {
            "label": engine.llm.title,
            "per_second": await live_engines.per_second(db, engine),
        }
    if engine is None:
        return Response(status_code=503)
    if engine.transport == "realtime":
        return await _open_realtime(offer, engine, info)
    session = {
        "model": engine.llm.model_id,
        "instructions": engine.instructions or "",
        "audio": {"output": {"voice": settings.TTS_VOICE or LIVE_FALLBACK_VOICE}},
        "delegation": {"type": "client"},
        "input": await _history(offer.conversation_id),
    }
    try:
        opened = await _live_api().create(
            session=session, transport={"type": "webrtc", "sdp": offer.sdp}
        )
    except Exception:
        logger.exception("Opening a live session failed")
        return Response(status_code=502)
    # Its ledger row opens with it (#270): every live minute is metered.
    await usage_recording.open_live_call(
        opened.session.id,
        model=engine.llm.model_id,
        conversation_id=offer.conversation_id,
        user_id=STANDALONE_USER_ID,
    )
    return JSONResponse(
        {
            "sdp": opened.transport.sdp,
            "session_id": opened.session.id,
            "transport": "gpt_live",
            "engine": info,
        }
    )


async def _open_realtime(
    offer: Offer, engine: LiveEngine, info: dict[str, Any] | None
) -> Response:
    """Her voice agent as the realtime model's brain. Its turns are saved
    into a conversation, so a call without one starts one."""
    model = live_engines.realtime_model(engine)
    conversation = (
        await owned(offer.conversation_id)
        if offer.conversation_id
        else await ai_service.conversation_manager.create_conversation(
            provider=AIProvider.OPENAI,
            model=model,
            user_id=STANDALONE_USER_ID,
            surface=SURFACE,
        )
    )
    try:
        sdp = await realtime_calls.open_call(
            offer.sdp,
            conversation=conversation,
            model=model,
            agent_slug=FINANCE_VOICE_AGENT_SLUG,
            user_id=STANDALONE_USER_ID,
            voice=settings.TTS_VOICE,
            instructions=engine.instructions,
            max_output_tokens=engine.max_output_tokens,
        )
    except Exception:
        logger.exception("Opening a realtime call failed")
        return Response(status_code=502)
    return JSONResponse(
        {
            "sdp": sdp,
            "transport": "realtime",
            "conversation_id": conversation.id,
            "engine": info,
        }
    )


@router.post(USAGE, include_in_schema=False)
async def billed(report: Billed) -> Response:
    """The page relays OpenAI's running total; the ledger keeps the largest.
    A closed tab loses only the seconds since the last report."""
    known = await usage_recording.live_call_seconds(
        report.session_id, report.seconds, reason=report.reason
    )
    return Response(status_code=204 if known else 404)


@router.post(DELEGATIONS, include_in_schema=False)
async def delegate(said: Said) -> Response:
    """Her voice agent's turn on what was said, as what GPT-Live says back.
    Run through the typed turn's stream, drained: ``chat()`` keeps no tool
    trace, model or cost, and the thread draws the trail, the approval
    cards and the footer from those. A failure is still something to say:
    the session stays up."""
    final = None
    try:
        async for frame in ai_service.stream_chat(
            message=said.text,
            conversation_id=said.conversation_id,
            user_id=STANDALONE_USER_ID,
            agent_slug=FINANCE_VOICE_AGENT_SLUG,
            surface=SURFACE,
        ):
            if frame.is_final:
                final = frame
    except Exception:
        logger.exception("A live delegation failed")
    if final is None:
        return JSONResponse({"speak": LIVE_SORRY, "conversation_id": None})
    return JSONResponse(
        {
            "speak": to_spoken(readable(final.content), LIVE_ANSWER_CHARS),
            "conversation_id": final.conversation_id,
        }
    )
