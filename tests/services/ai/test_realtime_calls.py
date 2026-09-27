"""A live call on a Pydantic AI realtime engine (#273).

Her own agent is the brain: prompt, tools, code mode. The server's
sideband sees every event; each finished turn is saved into the
conversation the way a typed turn is (what you said, what she said, the
steps she took), and the call's priced usage goes to the ledger.
"""

from __future__ import annotations

from types import SimpleNamespace

from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartEndEvent,
    RealtimeTurnCompleteEvent,
    SpeechPart,
    ToolCallPart,
    ToolReturnPart,
)
import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.components.backend.api.ai.router import ai_service
from app.services.ai import usage_recording
from app.services.ai.domains.voice.realtime_calls import TurnLog, save_turn
from app.services.ai.models import AIProvider
from app.services.ai.models.llm import LLMUsage
from app.services.finance.domains.detection.analyst.shared import STANDALONE_USER_ID
from tests._session import opens
from tests._voice_catalog import seed_voice_catalog


def _turn() -> list[object]:
    call = ToolCallPart(
        tool_name="run_code", args={"code": "await budget()"}, tool_call_id="t1"
    )
    result = ToolReturnPart(
        tool_name="run_code", content="{'net': -19939}", tool_call_id="t1"
    )
    return [
        PartEndEvent(
            index=0, part=SpeechPart(speaker="user", transcript="How much is left?")
        ),
        FunctionToolCallEvent(part=call),
        FunctionToolResultEvent(part=result),
        PartEndEvent(
            index=1,
            part=SpeechPart(speaker="assistant", transcript="You're $199.39 over."),
        ),
        RealtimeTurnCompleteEvent(),
    ]


class TestATurn:
    def test_a_turn_is_what_you_said_what_she_said_and_her_steps(self) -> None:
        log = TurnLog()
        done = [log.observe(event) for event in _turn()]
        assert done == [False, False, False, False, True]
        turn = log.take()
        assert turn is not None
        heard, said, trace = turn
        assert (heard, said) == ("How much is left?", "You're $199.39 over.")
        assert [step["tool"] for step in trace] == ["run_code"]
        assert trace[0]["code"] == "await budget()"
        assert log.take() is None  # taken once

    @pytest.mark.asyncio
    async def test_a_finished_turn_lands_in_the_conversation(self) -> None:
        conversation = await ai_service.conversation_manager.create_conversation(
            provider=AIProvider.OPENAI,
            model="gpt-realtime-2.1",
            user_id=STANDALONE_USER_ID,
            surface="finance",
        )
        log = TurnLog()
        for event in _turn():
            log.observe(event)
        turn = log.take()
        assert turn is not None
        heard, said, trace = turn

        await save_turn(conversation.id, heard, said, trace, model="gpt-realtime-2.1")

        stored = await ai_service.get_conversation(conversation.id)
        user, reply = stored.messages[-2:]
        assert user.content == "How much is left?"
        assert reply.content == "You're $199.39 over."
        assert reply.metadata["tool_trace"][0]["tool"] == "run_code"
        assert reply.metadata["model"] == "gpt-realtime-2.1"


class TestTheLedger:
    @pytest.mark.asyncio
    async def test_the_call_is_priced_from_its_usage(
        self, async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """At the catalog's rates: text, audio and cached input each at its
        own, then text and audio output (Pydantic AI's totals include the
        audio and the cached share)."""
        monkeypatch.setattr(
            usage_recording, "get_async_session", opens(async_db_session)
        )
        await seed_voice_catalog(async_db_session)
        usage = SimpleNamespace(
            input_tokens=15_299,
            input_audio_tokens=1_000,
            cache_read_tokens=12_000,
            cache_audio_read_tokens=0,
            output_tokens=887,
            output_audio_tokens=600,
            tool_calls=2,
        )

        await usage_recording.record_realtime(
            "gpt-realtime-2.1", usage, seconds=49.9, conversation_id="c-1"
        )

        (row,) = (await async_db_session.exec(select(LLMUsage))).all()
        assert (row.action, row.model_id, row.conversation_id) == (
            "realtime",
            "gpt-realtime-2.1",
            "c-1",
        )
        text_in, audio_in, cached = 2_299 * 4e-6, 1_000 * 32e-6, 12_000 * 0.4e-6
        text_out, audio_out = 287 * 24e-6, 600 * 64e-6
        assert row.total_cost == pytest.approx(
            text_in + audio_in + cached + text_out + audio_out
        )
        assert (row.input_tokens, row.output_tokens, row.tool_calls) == (15_299, 887, 2)
        assert row.audio_seconds == pytest.approx(49.9)
