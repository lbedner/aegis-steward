"""A live call on a Pydantic AI realtime engine (#273).

The realtime model IS her brain here: her voice agent's prompt, tools and
code mode, built exactly as a turn builds it. The browser's WebRTC offer
is answered by Pydantic AI; a server sideband attaches to the same call to
run her tools in a turn's context, save each finished turn into the
conversation (what you said, what she said, her steps - so the thread
shows it like a typed turn), and ledger the priced usage when it ends.
GPT-Live is the other transport (``chat_live.py``).
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartEndEvent,
    RealtimeTurnCompleteEvent,
    SpeechPart,
)

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
        """Take in one event; True when it finished a turn."""
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
) -> None:
    """The turn into the conversation, as a typed turn is kept."""
    from app.services.ai.deps import ai_service

    conversation = await ai_service.get_conversation(conversation_id)
    if conversation is None:
        return
    if heard:
        conversation.add_message(MessageRole.USER, heard)
    metadata: dict[str, Any] = {
        "conversation_id": conversation_id,
        "provider": "openai",
        "model": model,
        "stream_complete": True,
    }
    if trace:
        metadata["tool_trace"] = trace
    conversation.add_message(MessageRole.ASSISTANT, said, metadata=metadata)
    await ai_service.conversation_manager.save_conversation(conversation)


async def open_call(
    sdp: str,
    *,
    conversation: Any,
    model: str,
    agent_slug: str,
    user_id: str,
    voice: str | None,
    instructions: str | None = None,
    max_output_tokens: int | None = None,
) -> str:
    """Answer the browser's offer with her agent on ``model`` (a Pydantic AI
    realtime model string), and attach the sideband that runs the call.
    The engine's ``instructions`` (how to talk on a call) lead her prompt,
    and ``max_output_tokens`` caps one reply. Returns the SDP answer."""
    from app.services.ai.deps import ai_service
    from app.services.ai.domains.chat.agent_loader import resolve_agent

    config = await resolve_agent(agent_slug)
    if instructions:
        config = replace(
            config, system_prompt=f"{instructions}\n\n{config.system_prompt}"
        )
    settings: dict[str, Any] = {
        "openai_voice": voice or "marin",
        # What you said is only transcribed when asked for, and a saved
        # turn needs your side too. Its cost rides in the call's usage.
        "input_transcription_model": INPUT_TRANSCRIPTION,
    }
    if max_output_tokens:
        settings["max_tokens"] = max_output_tokens
    agent, history = ai_service._prepare_agent_and_context(
        conversation, agent_config=config
    )
    realtime = agent.realtime(
        model,
        instructions=history,
        model_settings=settings,  # type: ignore[arg-type]
    )
    answer = await realtime.answer_webrtc_offer(sdp)
    task = asyncio.create_task(
        _sideband(realtime, answer, conversation.id, model, agent_slug, user_id)
    )
    _running.add(task)
    task.add_done_callback(_running.discard)
    return answer.sdp


async def _sideband(
    realtime: Any,
    answer: Any,
    conversation_id: str,
    model: str,
    agent_slug: str,
    user_id: str,
) -> None:
    started = datetime.now(UTC)
    usage = None
    log = TurnLog()
    bare = model.split(":", 1)[-1]
    try:
        with (
            memory_user(
                user_id, agent_slug=agent_slug, conversation_id=conversation_id
            ),
            reading_stage(),
            card_stage() as drawn,
        ):
            log.drawn = drawn
            async with realtime.session(provider_session=answer.session) as session:
                try:
                    async for event in session:
                        if log.observe(event) and (turn := log.take()):
                            log.drawn = drawn
                            await save_turn(conversation_id, *turn, model=bare)
                finally:
                    # A dropped call still used what it used.
                    usage = session.usage
    except Exception:
        logger.exception("A realtime call ended with an error")
    finally:
        if usage is not None:
            await usage_recording.record_realtime(
                bare,
                usage,
                seconds=(datetime.now(UTC) - started).total_seconds(),
                conversation_id=conversation_id,
                user_id=user_id,
            )
