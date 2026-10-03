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

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, WebSocket
from pydantic import BaseModel, Field, field_validator
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    PartEndEvent,
    RealtimeResponseInterruptedEvent,
    RealtimeTurnCompleteEvent,
    SpeechPart,
)
from starlette.responses import JSONResponse, Response

from app.components.backend.api.ai.router import ai_service, sync_active_model
from app.components.web_frontend.rendering import templates
from app.components.web_frontend.routes.chat import SECTION, SURFACE, owned
from app.components.web_frontend.routes.chat_speech import transcription_hint
from app.core.chat_transcript import readable, trace_label
from app.core.config import settings
from app.core.db import get_async_session
from app.core.log import logger
from app.services.ai import usage_recording
from app.services.ai.domains.voice import live_engines, realtime_calls
from app.services.ai.domains.voice.spoken import to_spoken
from app.services.ai.models import AIProvider
from app.services.ai.models.live_engine import LiveEngine
from app.services.ai.service.trace import record_tool_call
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
ENGINE = LIVE + "/engine"
RELAY = LIVE + "/ws"

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
# A call into a conversation already under way (#292): every call opened
# "Lovely to meet you" with hundreds of turns behind it.
LIVE_CONTINUE = (
    "The call just connected, in a conversation you are already having with "
    "them (it is above). Greet them briefly as someone you know - no "
    "introductions - and ask what is next."
)
# A call soon after one dropped mid-answer picks it up (realtime_calls.
# resumed): she says they got cut off and carries on.
LIVE_RESUME = (
    "The call just reconnected. It dropped while you were answering them; "
    'they had asked: "{asked}". Say in a few words that you got cut off, '
    "then pick that answer up."
)


def _opening(conversation: Any) -> str:
    """How she opens the call: picking up a dropped one, carrying on a
    conversation, or a first greeting."""
    if conversation is None:
        return LIVE_GREETING
    if asked := realtime_calls.resumed(conversation):
        return LIVE_RESUME.format(asked=asked)
    return LIVE_CONTINUE if conversation.messages else LIVE_GREETING


# What the live button carries for voice.js: the sign-off and the tool it
# hangs up on, and what she says when the page cannot get an answer.
templates.env.globals["live_lines"] = {
    "sign_off": LIVE_SIGN_OFF,
    "end_call": realtime_calls.END_CALL,
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


async def _engine_now() -> tuple[LiveEngine | None, dict[str, Any] | None]:
    """The engine her voice profile names, and what the call bar shows of
    it: its name, and its rate when it bills by the second (a token-billed
    engine is priced after the call). Its own short session: SQLite
    sessions here BEGIN IMMEDIATE, and a call writes in others."""
    async with get_async_session() as db:
        engine = await live_engines.resolve(db, settings.VOICE_LIVE_ENGINE)
        info = engine and {
            "label": engine.llm.title,
            "per_second": await live_engines.per_second(db, engine),
        }
    return engine, info


@router.get(ENGINE, include_in_schema=False)
async def engine_now() -> Response:
    """How the phone dials: WebRTC (GPT-Live, OpenAI realtime) or the
    relay's WebSocket (Gemini Live)."""
    engine, info = await _engine_now()
    if engine is None:
        return Response(status_code=503)
    return JSONResponse({"transport": engine.transport, "engine": info})


@router.post(SESSIONS, include_in_schema=False)
async def open_session(offer: Offer) -> Response:
    """The browser's offer, answered by the engine her voice profile names
    (#273): GPT-Live handing real work back to the page, or a realtime
    model running her own agent. The answer says which, so the page
    speaks that engine's events."""
    engine, info = await _engine_now()
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
            "greeting": _opening(
                await owned(offer.conversation_id) if offer.conversation_id else None
            ),
        }
    )


async def _call(conversation_id: str | None, engine: LiveEngine) -> tuple[Any, Any]:
    """Her voice agent as a realtime engine's brain, and the conversation
    its turns are saved into - a call without one starts one."""
    model = live_engines.realtime_model(engine)
    conversation = (
        await owned(conversation_id)
        if conversation_id
        else await ai_service.conversation_manager.create_conversation(
            provider=AIProvider(model.split(":", 1)[0]),
            model=model,
            user_id=STANDALONE_USER_ID,
            surface=SURFACE,
        )
    )
    realtime = await realtime_calls.realtime_for(
        conversation=conversation,
        model=model,
        agent_slug=FINANCE_VOICE_AGENT_SLUG,
        voice=settings.TTS_VOICE,
        # A call's transcript takes no vocabulary hint, so she is told the
        # names instead, spelled (#293).
        instructions="\n\n".join(
            filter(
                None,
                [
                    engine.instructions,
                    f"Names you will hear, spelled: {await transcription_hint()}",
                ],
            )
        ),
        max_output_tokens=engine.max_output_tokens,
    )
    return conversation, realtime


def _driving(conversation: Any, engine: LiveEngine) -> dict[str, Any]:
    """Who the call is, for ``realtime_calls.drive``."""
    return {
        "conversation_id": conversation.id,
        "model": live_engines.realtime_model(engine),
        "agent_slug": FINANCE_VOICE_AGENT_SLUG,
        "user_id": STANDALONE_USER_ID,
    }


async def _open_realtime(
    offer: Offer, engine: LiveEngine, info: dict[str, Any] | None
) -> Response:
    """A realtime engine over WebRTC: Pydantic AI answers the offer, and
    the audio goes straight between the browser and the provider."""
    try:
        conversation, realtime = await _call(offer.conversation_id, engine)
        sdp = await realtime_calls.open_call(
            offer.sdp, realtime=realtime, **_driving(conversation, engine)
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
            "greeting": _opening(conversation),
        }
    )


# --- The relay (#273): Gemini Live has no WebRTC hand-off, only a
# WebSocket holding the key, so the call's audio comes through here. Mic
# audio arrives as binary frames (PCM16 at the model's input rate) and her
# voice goes back the same way (at its output rate); a few JSON events
# tell the page what the call is doing. ``drive`` does the rest, as for a
# WebRTC call: her tools, the saved turns, the priced usage.


async def _tell(ws: WebSocket, event: Any) -> None:
    """What the page shows of an event, if anything."""
    said: dict[str, Any] | None = None
    if isinstance(event, RealtimeResponseInterruptedEvent):
        said = {"type": "interrupted"}  # flush what has not been played
    elif isinstance(event, FunctionToolCallEvent):
        if event.part.tool_name == realtime_calls.END_CALL:
            said = {"type": "hang_up"}  # after her goodbye has played
        else:
            step: list[dict[str, Any]] = []
            record_tool_call(step, event)
            said = {"type": "working", "label": trace_label(step[0])}
    elif isinstance(event, RealtimeTurnCompleteEvent):
        said = {"type": "done"}
    elif isinstance(event, PartEndEvent) and isinstance(event.part, SpeechPart):
        side = "heard" if event.part.speaker == "user" else "said"
        said = {"type": side, "text": event.part.transcript or ""}
    if said:
        await _send(ws, said)


async def _send(ws: WebSocket, message: dict[str, Any]) -> None:
    try:
        await ws.send_json(message)
    except Exception:  # the page hung up first
        return


async def _carry(
    ws: WebSocket, session: Any, conversation: Any, info: dict[str, Any] | None
) -> None:
    """The audio, both ways, until the page hangs up."""
    await ws.send_json(
        {
            "type": "ready",
            "input_rate": session.audio_input_sample_rate,
            "output_rate": session.audio_output_sample_rate,
            "conversation_id": conversation.id,
            "engine": info,
        }
    )

    async def speak() -> None:
        async for chunk in session.stream_audio():
            await ws.send_bytes(chunk)

    voice = asyncio.create_task(speak())
    try:
        await session.send(_opening(conversation))
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes"):
                await session.send_audio(message["bytes"])
    finally:
        voice.cancel()
        await session.close()


@router.websocket(RELAY)
async def relay(ws: WebSocket, conversation_id: str | None = None) -> None:
    """A call on a relay engine, for as long as the page holds the socket."""
    await ws.accept()
    engine, info = await _engine_now()
    if engine is None or engine.transport != "relay":
        await ws.close(code=1008, reason="The live engine is not a relay engine.")
        return
    try:
        conversation, realtime = await _call(conversation_id, engine)
    except Exception:
        logger.exception("Opening a relay call failed")
        await ws.close(code=1011)
        return
    await realtime_calls.drive(
        realtime,
        alongside=lambda session: _carry(ws, session, conversation, info),
        on_event=lambda event: _tell(ws, event),
        on_saved=lambda cost: _send(ws, {"type": "saved", "cost": cost}),
        **_driving(conversation, engine),
    )
    try:
        await ws.close()
    except Exception:  # already closed by the page
        return


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
