"""What models and voice cost, read back (#270).

One ledger (``llm_usage``) holds chat turns, live calls, transcriptions
and spoken replies. The report groups it into four kinds, lists it by
model, and costs each live call with the answers given during it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.ai.models.llm import LLMUsage
from app.services.ai.usage_report import KIND_LABELS, usage_report

NOW = datetime(2026, 9, 27, 18, 0, tzinfo=UTC)


def _row(**fields: object) -> LLMUsage:
    base: dict[str, object] = {
        "model_id": "gpt-5.6-luna",
        "input_tokens": 0,
        "output_tokens": 0,
        "total_cost": 0.0,
        "timestamp": NOW,
        "action": "stream_chat:finance-assistant",
    }
    return LLMUsage(**{**base, **fields})


@pytest.fixture
async def ledger(async_db_session: AsyncSession) -> AsyncSession:
    call_start = NOW - timedelta(hours=1)
    async_db_session.add_all(
        [
            # A typed turn.
            _row(input_tokens=1_000, output_tokens=200, total_cost=0.02),
            # A ten-minute live call, and two answers during it.
            _row(
                action="live",
                model_id="gpt-live-1",
                session_id="live_1",
                conversation_id="c-1",
                audio_seconds=600,
                total_cost=0.50,
                timestamp=call_start,
            ),
            _row(
                action="stream_chat:finance-voice",
                conversation_id="c-1",
                total_cost=0.03,
                timestamp=call_start + timedelta(minutes=2),
            ),
            _row(
                action="stream_chat:finance-voice",
                conversation_id="c-1",
                total_cost=0.04,
                timestamp=call_start + timedelta(minutes=8),
            ),
            # The same conversation typed into a day later is not the call.
            _row(
                action="stream_chat:finance-assistant",
                conversation_id="c-1",
                total_cost=0.05,
                timestamp=call_start + timedelta(days=1),
            ),
            _row(
                action="stt",
                model_id="gpt-transcribe",
                audio_seconds=60,
                total_cost=0.0045,
            ),
            _row(action="tts", model_id="gpt-4o-mini-tts", total_cost=0.01),
            # Outside the window.
            _row(total_cost=9.0, timestamp=NOW - timedelta(days=45)),
        ]
    )
    await async_db_session.commit()
    return async_db_session


class TestTheReport:
    @pytest.mark.asyncio
    async def test_everything_is_one_of_four_kinds(self, ledger: AsyncSession) -> None:
        report = await usage_report(ledger, days=30, now=NOW + timedelta(days=1))
        kinds = {k["key"]: k for k in report["kinds"]}
        assert list(kinds) == list(KIND_LABELS)
        assert kinds["chat"]["cost"] == pytest.approx(0.02 + 0.03 + 0.04 + 0.05)
        assert kinds["live"]["cost"] == pytest.approx(0.50)
        assert kinds["live"]["minutes"] == pytest.approx(10)
        assert kinds["stt"]["minutes"] == pytest.approx(1)
        assert kinds["tts"]["cost"] == pytest.approx(0.01)
        assert report["total"] == pytest.approx(0.14 + 0.50 + 0.0045 + 0.01)

    @pytest.mark.asyncio
    async def test_by_model_most_expensive_first(self, ledger: AsyncSession) -> None:
        report = await usage_report(ledger, days=30, now=NOW + timedelta(days=1))
        assert [m["model"] for m in report["models"]][:2] == [
            "gpt-live-1",
            "gpt-5.6-luna",
        ]

    @pytest.mark.asyncio
    async def test_a_call_costs_its_minutes_plus_its_answers(
        self, ledger: AsyncSession
    ) -> None:
        report = await usage_report(ledger, days=30, now=NOW + timedelta(days=1))
        (call,) = report["calls"]
        assert call["minutes"] == pytest.approx(10)
        assert call["call_cost"] == pytest.approx(0.50)
        # The two answers during the call, not the typed turn a day later.
        assert call["answers_cost"] == pytest.approx(0.07)
        assert call["total"] == pytest.approx(0.57)

    @pytest.mark.asyncio
    async def test_a_realtime_call_is_a_live_call_too(
        self, ledger: AsyncSession
    ) -> None:
        ledger.add(
            _row(
                action="realtime",
                model_id="gpt-realtime-2.1",
                conversation_id="c-2",
                audio_seconds=120,
                total_cost=0.17,
                input_tokens=30_000,
                timestamp=NOW,
            )
        )
        await ledger.commit()
        report = await usage_report(ledger, days=30, now=NOW + timedelta(days=1))
        kinds = {k["key"]: k for k in report["kinds"]}
        assert kinds["live"]["cost"] == pytest.approx(0.50 + 0.17)
        assert {c["model"] for c in report["calls"]} == {
            "gpt-live-1",
            "gpt-realtime-2.1",
        }
