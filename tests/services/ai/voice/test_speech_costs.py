"""Hearing and speaking cost money too (#270).

Every transcription and every spoken reply was recorded at $0 (a TODO in
both services). Each is now priced from one rate table and written to
the one usage ledger (``llm_usage``, action ``stt``/``tts``) beside the
model calls and live minutes, and to its own telemetry row.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

import app.core.db as db_module
from app.services.ai import usage_recording
from app.services.ai.domains.voice.stt import STTService
from app.services.ai.domains.voice.tts import TTSService
from app.services.ai.models.llm import LLMUsage
from app.services.ai.models.voice_usage import STTUsage, TTSUsage
from tests._session import opens


class TestRates:
    @pytest.mark.parametrize(
        ("model", "measure", "cost"),
        [
            ("gpt-transcribe", {"seconds": 60}, 0.0045),
            ("gpt-4o-mini-transcribe", {"seconds": 60}, 0.003),
            ("gpt-live-1", {"seconds": 60}, 0.05),
            ("tts-1", {"characters": 1_000_000}, 15.0),
            # A dated build prices as its model.
            ("gpt-4o-mini-tts-2025-03-20", {"characters": 900}, 0.015),
            ("never-heard-of-it", {"seconds": 60}, 0.0),
        ],
    )
    def test_a_measure_is_priced_by_its_model(
        self, model: str, measure: dict[str, float], cost: float
    ) -> None:
        assert usage_recording.rated_cost(model, **measure) == pytest.approx(cost)

    def test_nothing_measured_costs_nothing(self) -> None:
        assert usage_recording.rated_cost("gpt-transcribe") == 0.0


@pytest.fixture
def ledger(
    async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncSession:
    monkeypatch.setattr(db_module, "get_async_session", opens(async_db_session))
    monkeypatch.setattr(usage_recording, "get_async_session", opens(async_db_session))
    return async_db_session


def _settings(**values: object) -> MagicMock:
    settings = MagicMock()
    for key, value in values.items():
        setattr(settings, key, value)
    return settings


class TestHearing:
    @pytest.mark.asyncio
    async def test_a_transcription_is_priced_and_in_the_ledger(
        self, ledger: AsyncSession
    ) -> None:
        stt = STTService(
            _settings(
                STT_PROVIDER="openai_whisper",
                STT_MODEL="gpt-transcribe",
                STT_LANGUAGE=None,
                STT_DEVICE=None,
            )
        )
        await stt._record_usage(
            input_bytes=48_000,
            input_duration_seconds=30,
            output_characters=120,
            detected_language="en",
            latency_ms=900,
            user_id="0",
            success=True,
            error_message=None,
        )

        (row,) = (await ledger.exec(select(LLMUsage))).all()
        assert (row.action, row.model_id, row.audio_seconds) == (
            "stt",
            "gpt-transcribe",
            30,
        )
        assert row.total_cost == pytest.approx(0.00225)
        (telemetry,) = (await ledger.exec(select(STTUsage))).all()
        assert telemetry.total_cost == pytest.approx(0.00225)


class TestSpeaking:
    @pytest.mark.asyncio
    async def test_a_spoken_reply_is_priced_by_its_characters(
        self, ledger: AsyncSession
    ) -> None:
        tts = TTSService(
            _settings(
                TTS_PROVIDER="openai",
                TTS_MODEL="tts-1",
                TTS_VOICE="marin",
                TTS_SPEED=1.0,
                TTS_INSTRUCTIONS=None,
            )
        )
        await tts._record_usage(
            input_characters=2_000,
            output_bytes=64_000,
            output_duration_seconds=None,
            voice="marin",
            latency_ms=700,
            user_id="0",
            success=True,
            error_message=None,
        )

        (row,) = (await ledger.exec(select(LLMUsage))).all()
        assert (row.action, row.model_id) == ("tts", "tts-1")
        assert row.total_cost == pytest.approx(0.03)
        (telemetry,) = (await ledger.exec(select(TTSUsage))).all()
        assert telemetry.total_cost == pytest.approx(0.03)
