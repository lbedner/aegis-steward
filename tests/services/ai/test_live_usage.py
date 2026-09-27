"""Live voice in the usage ledger (#270): one table for what models cost.

A GPT-Live call is a row in ``llm_usage`` like any model call. It bills
seconds, not tokens (``audio_seconds``, tokens 0 because it reports none),
and her answers during the call are their own rows in the same
conversation, so a call's cost is its row plus its turns.
"""

from __future__ import annotations

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.ai import usage_recording
from app.services.ai.models.llm import LLMUsage
from tests._session import opens


@pytest.fixture(autouse=True)
def _ledger_uses_test_session(
    async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage_recording, "get_async_session", opens(async_db_session))


async def _rows(db: AsyncSession) -> list[LLMUsage]:
    return list((await db.exec(select(LLMUsage))).all())


class TestACall:
    @pytest.mark.asyncio
    async def test_opening_a_call_starts_its_row(
        self, async_db_session: AsyncSession
    ) -> None:
        await usage_recording.open_live_call(
            "live_1", model="gpt-live-1", conversation_id="c-1", user_id="0"
        )
        (row,) = await _rows(async_db_session)
        assert (row.model_id, row.action, row.session_id, row.conversation_id) == (
            "gpt-live-1",
            "live",
            "live_1",
            "c-1",
        )
        assert (row.audio_seconds, row.total_cost, row.input_tokens) == (0.0, 0.0, 0)

    @pytest.mark.asyncio
    async def test_its_seconds_are_billed_at_the_live_rate(
        self, async_db_session: AsyncSession
    ) -> None:
        await usage_recording.open_live_call("live_1", model="gpt-live-1")
        await usage_recording.live_call_seconds("live_1", 600)  # ten minutes
        (row,) = await _rows(async_db_session)
        assert row.audio_seconds == 600
        assert row.total_cost == pytest.approx(0.50)

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # two reports on one call, on purpose
    async def test_the_total_is_cumulative_and_never_goes_back(
        self, async_db_session: AsyncSession
    ) -> None:
        """OpenAI reports a running total ("do not sum"); a late, smaller
        report must not undo a larger one."""
        await usage_recording.open_live_call("live_1", model="gpt-live-1")
        await usage_recording.live_call_seconds("live_1", 120)
        await usage_recording.live_call_seconds("live_1", 90)
        (row,) = await _rows(async_db_session)
        assert row.audio_seconds == 120

    @pytest.mark.asyncio
    async def test_a_call_that_ended_badly_says_why(
        self, async_db_session: AsyncSession
    ) -> None:
        await usage_recording.open_live_call("live_1", model="gpt-live-1")
        await usage_recording.live_call_seconds("live_1", 30, reason="connection_lost")
        (row,) = await _rows(async_db_session)
        assert (row.success, row.error_message) == (False, "connection_lost")

    @pytest.mark.asyncio
    async def test_hanging_up_is_a_good_ending(
        self, async_db_session: AsyncSession
    ) -> None:
        await usage_recording.open_live_call("live_1", model="gpt-live-1")
        await usage_recording.live_call_seconds("live_1", 30, reason="close_requested")
        (row,) = await _rows(async_db_session)
        assert (row.success, row.error_message) == (True, None)

    @pytest.mark.asyncio
    async def test_an_unknown_call_is_refused(self) -> None:
        assert await usage_recording.live_call_seconds("nope", 30) is False


class TestHerAnswers:
    @pytest.mark.asyncio
    async def test_a_turn_names_its_conversation(
        self, async_db_session: AsyncSession
    ) -> None:
        await usage_recording.record_usage(
            "stream_chat:finance-voice",
            "gpt-5.6-luna",
            {"input_tokens": 10, "output_tokens": 5},
            "0",
            conversation_id="c-1",
        )
        (row,) = await _rows(async_db_session)
        assert row.conversation_id == "c-1"
