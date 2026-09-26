"""A spoken turn is a typed turn with a microphone in front of it.

#246: the voice pipeline must run through the same agent, user, surface
and conversation as text, so the same question gets the same answer and
lands in the same history whichever way it was asked. Before this, the
voice mixin was the "voice off" placeholder - the /ai/voice-chat route
called a method the service did not have - and the stack's version chatted
as the default agent and ran ``prepare_for_voice`` as a second, persisted
conversation owned by ``system_voice_convert``.
"""

from typing import Any

from pydantic_ai.messages import ModelResponse, SystemPromptPart, TextPart
from pydantic_ai.models.function import FunctionModel
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.services.ai.domains.chat.agent_registry import invalidate_agent_cache
from app.services.ai.domains.voice import STTService, TTSService
from app.services.ai.domains.voice.models import (
    AudioFormat,
    AudioInput,
    SpeechRequest,
    SpeechResult,
    STTProvider,
    TranscriptionResult,
    TTSProvider,
)
from app.services.ai.models import Agent
from app.services.ai.service import AIService
from app.services.finance.domains.detection.analyst.seeds import (
    FINANCE_CHAT_SYSTEM_PROMPT,
    finance_chat_agent_definition,
)

AGENT = "finance-assistant"
SURFACE = "finance"
OWNER = "0"
QUESTION = "How much is left in Vanessa's envelope?"
ANSWER = "## Vanessa\n\n- **$212** left this week."


class FakeSTT:
    def __init__(self) -> None:
        self.calls: list[tuple[AudioInput, str | None]] = []

    async def transcribe(
        self, audio: AudioInput, user_id: str | None = None
    ) -> TranscriptionResult:
        self.calls.append((audio, user_id))
        return TranscriptionResult(text=QUESTION, provider=STTProvider.OPENAI_WHISPER)


class FakeTTS:
    def __init__(self) -> None:
        self.calls: list[tuple[SpeechRequest, str | None]] = []

    async def synthesize(
        self, request: SpeechRequest, user_id: str | None = None
    ) -> SpeechResult:
        self.calls.append((request, user_id))
        return SpeechResult(
            audio=b"mp3", format=AudioFormat.MP3, provider=TTSProvider.OPENAI
        )


def _voiced(service: AIService) -> tuple[FakeSTT, FakeTTS]:
    stt, tts = FakeSTT(), FakeTTS()
    service._stt_service = stt  # type: ignore[assignment]
    service._tts_service = tts  # type: ignore[assignment]
    return stt, tts


@pytest.fixture(autouse=True)
def _clean_agent_cache() -> Any:
    invalidate_agent_cache()
    yield
    invalidate_agent_cache()


def test_the_service_can_hear_and_speak() -> None:
    """The routes call ``ai_service.stt`` / ``.tts`` / ``.voice_chat``;
    the service shipped in #245 had none of them."""
    service = AIService(settings)
    assert isinstance(service.stt, STTService)
    assert isinstance(service.tts, TTSService)
    assert callable(service.voice_chat)


@pytest.mark.asyncio
# Two turns, so every per-turn query (the agent row, her memory modules)
# runs exactly twice by design; three would still be an N+1 inside a turn.
@pytest.mark.queryspy(threshold=3)
async def test_a_spoken_turn_is_the_typed_turn(
    app_owned_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Typed, then spoken, in one conversation: both turns reach the model
    as Illiana (her prompt), both land in that conversation, and nothing
    else is written."""
    # The app-owned database is shared by the worker's tests; another may
    # have seeded her already, so her row is made to match the seed.
    maker = async_sessionmaker(app_owned_engine, class_=AsyncSession)
    definition = finance_chat_agent_definition()
    async with maker() as setup:
        row = (
            await setup.exec(select(Agent).where(Agent.slug == definition["slug"]))
        ).first()
        if row is None:
            setup.add(Agent(**definition))
        else:
            for field, value in definition.items():
                setattr(row, field, value)
        await setup.commit()

    prompts: list[str] = []

    async def _respond(messages: Any, info: Any) -> ModelResponse:
        prompts.append(
            "\n".join(
                part.content
                for message in messages
                for part in getattr(message, "parts", [])
                if isinstance(part, SystemPromptPart)
            )
        )
        return ModelResponse(parts=[TextPart(ANSWER)])

    # Pinned to Ollama and only its model swapped, so the agent itself
    # (prompt, tools, grants) is built the real way. Pinned because CI has
    # no .env: the default provider is the keyless public tier, and this
    # test once sent both turns to it for real (2026-09-25, 67k tokens).
    monkeypatch.setattr(settings, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(
        "app.services.ai.domains.llm.agents._ollama_model",
        lambda config, settings: FunctionModel(_respond),
    )

    service = AIService(settings)
    stt, tts = _voiced(service)

    typed = await service.chat(
        message=QUESTION, user_id=OWNER, agent_slug=AGENT, surface=SURFACE
    )
    conversation_id = typed.metadata["conversation_id"]
    spoken = await service.voice_chat(
        audio=AudioInput(content=b"wav", format=AudioFormat.WAV),
        conversation_id=conversation_id,
        user_id=OWNER,
        agent_slug=AGENT,
        surface=SURFACE,
        return_audio=True,
    )

    assert spoken.conversation_id == conversation_id
    assert spoken.full_response == typed.content == ANSWER
    assert len(prompts) == 2
    assert all(FINANCE_CHAT_SYSTEM_PROMPT[:80] in prompt for prompt in prompts)

    conversation = await service.get_conversation(conversation_id)
    assert conversation is not None
    assert [m.content for m in conversation.messages] == [
        QUESTION,
        ANSWER,
        QUESTION,
        ANSWER,
    ]
    assert await service.list_conversations("system_voice_convert") == []

    # Usage is the speaker's, on both sides of the model.
    assert stt.calls[0][1] == OWNER
    assert tts.calls[0][1] == OWNER
    assert spoken.audio_response == b"mp3"


@pytest.mark.asyncio
async def test_the_turn_carries_its_context_and_the_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AIService(settings)
    stt, _ = _voiced(service)
    seen: dict[str, Any] = {}

    async def _chat(**kwargs: Any) -> Any:
        seen.update(kwargs)
        from app.services.ai.models import ConversationMessage, MessageRole

        return ConversationMessage(
            id="m-1",
            role=MessageRole.ASSISTANT,
            content="ok",
            metadata={"conversation_id": "c-1"},
        )

    monkeypatch.setattr(service, "chat", _chat)
    await service.voice_chat(
        audio=AudioInput(content=b"wav", format=AudioFormat.WAV),
        conversation_id="c-1",
        user_id=OWNER,
        agent_slug=AGENT,
        surface=SURFACE,
        transcription_hint="Illiana, Vanessa",
    )

    assert seen == {
        "message": QUESTION,
        "conversation_id": "c-1",
        "user_id": OWNER,
        "agent_slug": AGENT,
        "surface": SURFACE,
    }
    assert stt.calls[0][0].prompt == "Illiana, Vanessa"


@pytest.mark.asyncio
async def test_speaking_an_answer_asks_no_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stack rewrote answers for speech with a second chat turn, as a
    persisted conversation outside the agent. Markdown is stripped here,
    and the agent's own words are what gets said."""
    service = AIService(settings)

    async def _no_chat(**kwargs: Any) -> Any:
        raise AssertionError("prepare_for_voice must not start a conversation")

    monkeypatch.setattr(service, "chat", _no_chat)
    spoken = await service.prepare_for_voice(
        "## Vanessa\n\n- **$212** left, see [the envelope](/budget).\n"
        "```python\nx = 1\n```\n1. Next: `groceries`"
    )

    assert spoken == "Vanessa. $212 left, see the envelope. Next: groceries"


@pytest.mark.parametrize(
    "hostile",
    [
        "[" * 40_000,
        "[\\" * 20_000,
        "](" * 20_000,
        " " * 40_000 + "x",
        "|" + " " * 40_000,
    ],
)
def test_stripping_is_linear_on_hostile_text(hostile: str) -> None:
    """CodeQL py/polynomial-redos on the link and table-rule patterns: text
    the model wrote is spoken through these, so they must not go
    quadratic on a long run of brackets or spaces."""
    import time

    from app.services.ai.domains.voice.spoken import to_spoken

    start = time.perf_counter()
    to_spoken(hostile)
    assert time.perf_counter() - start < 0.5


def test_links_and_tables_still_read_as_words() -> None:
    from app.services.ai.domains.voice.spoken import to_spoken

    said = to_spoken(
        "See [the envelope](/budget/1) and ![a chart](x.png).\n| a | b |\n|---|:--|\n| 1 | 2 |"
    )
    assert said == "See the envelope and a chart. a b. 1 2"
