"""A live call on a Pydantic AI realtime engine (#273).

The realtime model IS her brain here: her voice agent's prompt, tools and
code mode, built exactly as a turn builds it. Two ways the audio travels:
the browser's WebRTC offer answered by Pydantic AI (OpenAI), with a server
sideband attached to the same call; or the relay (Gemini Live), where our
server holds the session and carries the audio (``chat_live.py``). Either
way ``drive`` runs her tools in a turn's context, saves each finished turn
into the conversation (what you said, what she said, her steps - so the
thread shows it like a typed turn), and ledgers the priced usage when it
ends. GPT-Live is the third transport, hand-built (``chat_live.py``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartEndEvent,
    RealtimeTurnCompleteEvent,
    SpeechPart,
)
from pydantic_ai.toolsets import FunctionToolset

from app.core.log import logger
from app.services.ai import usage_recording
from app.services.ai.domains.chat.cards import attach_cards, card_stage
from app.services.ai.domains.chat.readings import reading_stage
from app.services.ai.domains.chat.user_memory import memory_user
from app.services.ai.models import MessageRole
from app.services.ai.service.trace import record_tool_call, record_tool_result

# The sideband sessions in flight: a task nothing holds is collected.
INPUT_TRANSCRIPTION = "gpt-4o-mini-transcribe"
_running: set[asyncio.Task[None]] = set()


def end_call() -> str:
    """End the call. Use it once they are done - a goodbye, "that's all",
    thanks with nothing more to ask - right after your short goodbye, and
    say nothing after it."""
    return "The call ends when your goodbye has played. Say nothing more."


# A call ends on her tool, not on a phrase the page listens for: a model
# can drop a phrase (Gemini said "Have a great day!" for "Talk soon."). The
# page hangs up when it sees the call (``chat_live``, voice.js).
END_CALL = end_call.__name__
CALL_TOOLS = FunctionToolset([end_call])

# How long a dropped call stays one to pick up: a call within this of the
# cut resumes it ("we got cut off - you were asking..."); after, it is a
# new call. The conversation is the memory; this is only its age.
RESUME_WINDOW = timedelta(minutes=15)


class TurnLog:
    """One turn, gathered from the session's events: what you said, what
    she said, and her steps, recorded as a streamed turn records them."""

    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> None:
        self.heard: list[str] = []
        self.said: list[str] = []
        self.trace: list[dict[str, Any]] = []
        self.drawn: list[dict[str, Any]] = []

    def observe(self, event: Any) -> bool:
        """Take in one event; True when it finished a turn. Ending the call
        is how it ends, not one of her steps."""
        if (
            isinstance(event, FunctionToolCallEvent | FunctionToolResultEvent)
            and event.part.tool_name == END_CALL
        ):
            return False
        if isinstance(event, PartEndEvent) and isinstance(event.part, SpeechPart):
            if event.part.transcript:
                side = self.heard if event.part.speaker == "user" else self.said
                side.append(event.part.transcript.strip())
        elif isinstance(event, FunctionToolCallEvent):
            record_tool_call(self.trace, event)
        elif isinstance(event, FunctionToolResultEvent):
            record_tool_result(self.trace, event)
            attach_cards(self.trace, self.drawn)
        return isinstance(event, RealtimeTurnCompleteEvent)

    def take(self) -> tuple[str, str, list[dict[str, Any]]] | None:
        """The finished turn, once; None when nothing was said."""
        heard, said, trace = " ".join(self.heard), " ".join(self.said), self.trace
        self._reset()
        if not heard and not said:
            return None
        return heard, said, trace


async def save_turn(
    conversation_id: str,
    heard: str,
    said: str,
    trace: list[dict[str, Any]],
    *,
    model: str,
    provider: str,
    usage: dict[str, Any] | None = None,
    interrupted: bool = False,
) -> None:
    """The turn into the conversation, as a typed turn is kept: ``usage``
    is its share of the call (tokens and cost), for its footer. A turn the
    call dropped in the middle of is ``interrupted``, and keeps what you
    asked so the next call can pick it up (``resumed``)."""
    from app.services.ai.deps import ai_service

    conversation = await ai_service.get_conversation(conversation_id)
    if conversation is None:
        return
    if heard:
        conversation.add_message(MessageRole.USER, heard)
    metadata: dict[str, Any] = {
        "conversation_id": conversation_id,
        "provider": provider,
        "model": model,
        "stream_complete": True,
    }
    if trace:
        metadata["tool_trace"] = trace
    metadata.update(usage or {})
    if interrupted:
        metadata.update(interrupted=True, asked=heard)
    conversation.add_message(MessageRole.ASSISTANT, said, metadata=metadata)
    await ai_service.conversation_manager.save_conversation(conversation)


def _model_settings(
    model: str, voice: str | None, max_output_tokens: int | None
) -> dict[str, Any]:
    """The session's settings for its provider. OpenAI transcribes what you
    said only when asked (a saved turn needs your side too; its cost rides
    in the call's usage) and speaks the profile's voice; Gemini transcribes
    by default and speaks its own."""
    # ponytail: Gemini's default voice; the engine row carries a voice
    # (Kore, Puck, ... - ``google_voice``) once voices are picked per engine.
    settings: dict[str, Any] = {}
    if model.startswith("openai:"):
        settings["openai_voice"] = voice or "marin"
        settings["input_transcription_model"] = INPUT_TRANSCRIPTION
    if model.startswith("google:"):
        # A TV in the room held Gemini's turn open: its default detector
        # heard "speech", never decided you had finished, and never
        # answered until you hung up (2026-09-30). Stricter about what
        # STARTS a turn (background talk is quieter than you), quicker to
        # END one.
        settings["google_vad"] = {"start_sensitivity": "low", "end_sensitivity": "high"}
    if max_output_tokens:
        settings["max_tokens"] = max_output_tokens
    return settings


def resumed(conversation: Any, now: datetime | None = None) -> str | None:
    """What you had asked when the last call dropped, if it dropped within
    ``RESUME_WINDOW`` and you had asked something; else None."""
    if not conversation.messages:
        return None
    last = conversation.messages[-1]
    meta = last.metadata or {}
    if last.role != MessageRole.ASSISTANT or not meta.get("interrupted"):
        return None
    at = last.timestamp if last.timestamp.tzinfo else last.timestamp.replace(tzinfo=UTC)
    if (now or datetime.now(UTC)) - at > RESUME_WINDOW:
        return None
    return meta.get("asked") or None


async def realtime_for(
    *,
    conversation: Any,
    model: str,
    agent_slug: str,
    voice: str | None,
    instructions: str | None = None,
    max_output_tokens: int | None = None,
) -> Any:
    """Her agent on ``model`` (a Pydantic AI realtime model string), built
    exactly as a turn builds it. The engine's ``instructions`` (how to talk
    on a call) lead her prompt, and ``max_output_tokens`` caps one reply."""
    from app.services.ai.deps import ai_service
    from app.services.ai.domains.chat.agent_loader import resolve_agent

    config = await resolve_agent(agent_slug)
    if instructions:
        config = replace(
            config, system_prompt=f"{instructions}\n\n{config.system_prompt}"
        )
    agent, history = ai_service._prepare_agent_and_context(
        conversation, agent_config=config
    )
    return agent.realtime(
        model,
        instructions=history,
        model_settings=_model_settings(model, voice, max_output_tokens),  # type: ignore[arg-type]
        toolsets=[CALL_TOOLS],
    )


async def open_call(
    sdp: str,
    *,
    realtime: Any,
    conversation_id: str,
    model: str,
    agent_slug: str,
    user_id: str,
) -> str:
    """Answer the browser's WebRTC offer, and attach the sideband that runs
    the call (``drive``). Returns the SDP answer."""
    answer = await realtime.answer_webrtc_offer(sdp)
    task = asyncio.create_task(
        drive(
            realtime,
            provider_session=answer.session,
            conversation_id=conversation_id,
            model=model,
            agent_slug=agent_slug,
            user_id=user_id,
        )
    )
    _running.add(task)
    task.add_done_callback(_running.discard)
    return answer.sdp


async def drive(
    realtime: Any,
    *,
    conversation_id: str,
    model: str,
    agent_slug: str,
    user_id: str,
    provider_session: Any = None,
    alongside: Callable[[Any], Awaitable[None]] | None = None,
    on_event: Callable[[Any], Awaitable[None]] | None = None,
    on_saved: Callable[[float], Awaitable[None]] | None = None,
) -> None:
    """Run one call to its end: her tools in a turn's context, each
    finished turn saved into the conversation, the priced usage ledgered.
    The session is the WebRTC call's sideband (``provider_session``), or
    the relay's own; ``alongside`` runs beside the events for the length
    of the call (the relay's audio), ``on_event`` sees every event and
    ``on_saved`` hears of each saved turn, with the call's cost so far."""
    started = datetime.now(UTC)
    usage = None
    log = TurnLog()
    provider, _, bare = model.rpartition(":")
    # The call so far, at the last saved turn: each turn keeps its share.
    so_far = {"input_tokens": 0, "output_tokens": 0, "cost": 0.0}
    price = await usage_recording.realtime_price(bare)

    async def keep(
        session: Any, turn: tuple[str, str, list[dict[str, Any]]], interrupted: bool
    ) -> float:
        """Save one turn with its share of the call; the call's cost so far."""
        nonlocal so_far
        now = {
            "input_tokens": session.usage.input_tokens or 0,
            "output_tokens": session.usage.output_tokens or 0,
            "cost": usage_recording.realtime_cost(price, session.usage),
        }
        await save_turn(
            conversation_id,
            *turn,
            model=bare,
            provider=provider or "openai",
            usage={k: now[k] - so_far[k] for k in now},
            interrupted=interrupted,
        )
        so_far = now
        return float(now["cost"])

    try:
        with (
            memory_user(
                user_id, agent_slug=agent_slug, conversation_id=conversation_id
            ),
            reading_stage(),
            card_stage() as drawn,
        ):
            log.drawn = drawn
            async with realtime.session(provider_session=provider_session) as session:
                beside = asyncio.create_task(alongside(session)) if alongside else None
                try:
                    async for event in session:
                        if on_event:
                            await on_event(event)
                        if log.observe(event) and (turn := log.take()):
                            log.drawn = drawn
                            cost = await keep(session, turn, interrupted=False)
                            if on_saved:
                                await on_saved(cost)
                finally:
                    # A dropped call still used what it used, and still
                    # said and ran what it did: the turn it cut is kept.
                    usage = session.usage
                    if cut := log.take():
                        try:
                            await keep(session, cut, interrupted=True)
                        except Exception:
                            logger.exception("Saving a cut-off turn failed")
                    if beside:
                        beside.cancel()
    except Exception:
        logger.exception("A realtime call ended with an error")
    finally:
        if usage is not None:
            await usage_recording.record_realtime(
                bare,
                usage,
                seconds=(datetime.now(UTC) - started).total_seconds(),
                conversation_id=conversation_id,
                price=price,
                user_id=user_id,
            )
