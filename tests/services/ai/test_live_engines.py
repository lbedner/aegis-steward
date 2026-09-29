"""Her live engines, as table data (#273).

A live call runs on an engine: GPT-Live (full duplex, our own client
delegation) or a Pydantic AI realtime model (turn-taking, her own agent
as the brain). Engines are rows, like agents and voice profiles: seeded
when missing, never overwritten once edited. The model an engine runs
on - its name, its maker, its price - is the catalog's row.
"""

from __future__ import annotations

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.ai.domains.voice import live_engines
from app.services.ai.domains.voice.profiles import SETTINGS
from app.services.ai.models.live_engine import LiveEngine
from app.services.finance.domains.detection.analyst.live_engines import ENGINE_SEEDS
from app.services.finance.domains.detection.analyst.prompts import LIVE_SIGN_OFF
from tests._voice_catalog import seed_voice_catalog


@pytest.fixture
async def seeded(async_db_session: AsyncSession) -> AsyncSession:
    await seed_voice_catalog(async_db_session)
    await live_engines.seed_missing(async_db_session, ENGINE_SEEDS)
    return async_db_session


async def _keyed(session: AsyncSession) -> dict[str, LiveEngine]:
    return {row.key: row for row in await live_engines.enabled(session)}


class TestTheRows:
    @pytest.mark.asyncio
    async def test_the_engines_on_offer(self, seeded: AsyncSession) -> None:
        assert list(await _keyed(seeded)) == [
            "gpt-live",
            "gpt-realtime-2.1",
            "gpt-realtime-2.1-mini",
        ]
        assert live_engines.DEFAULT == "gpt-live"

    @pytest.mark.asyncio
    async def test_each_says_how_it_connects(self, seeded: AsyncSession) -> None:
        rows = await _keyed(seeded)
        live, realtime = rows["gpt-live"], rows["gpt-realtime-2.1"]
        assert (live.transport, live.llm.model_id) == ("gpt_live", "gpt-live-1")
        assert realtime.transport == "realtime"
        assert live_engines.realtime_model(realtime) == "openai:gpt-realtime-2.1"

    @pytest.mark.asyncio
    async def test_an_edit_survives_the_next_seed(self, seeded: AsyncSession) -> None:
        row = (await _keyed(seeded))["gpt-realtime-2.1"]
        row.instructions = "Be terse."
        seeded.add(row)
        await seeded.commit()

        await live_engines.seed_missing(seeded, ENGINE_SEEDS)

        stored = await seeded.exec(
            select(LiveEngine.instructions).where(LiveEngine.key == row.key)
        )
        assert stored.one() == "Be terse."


class TestHowTheyTalk:
    @pytest.mark.asyncio
    async def test_gpt_live_carries_its_persona_and_its_rate(
        self, seeded: AsyncSession
    ) -> None:
        live = (await _keyed(seeded))["gpt-live"]
        assert LIVE_SIGN_OFF in (live.instructions or "")
        # The catalog's rate for its model, not a copy on the engine.
        assert await live_engines.per_second(seeded, live) == pytest.approx(0.05 / 60)

    @pytest.mark.asyncio
    async def test_a_realtime_engine_knows_it_is_a_live_call(
        self, seeded: AsyncSession
    ) -> None:
        """Her voice agent's prompt is mostly her chat prompt; on a realtime
        engine the model speaks it straight out, and she went on. Each
        realtime row leads with a live-call section and caps a reply."""
        realtime = (await _keyed(seeded))["gpt-realtime-2.1"]
        assert "LIVE" in (realtime.instructions or "")
        assert LIVE_SIGN_OFF in (realtime.instructions or "")
        assert realtime.max_output_tokens
        assert await live_engines.per_second(seeded, realtime) is None  # tokens


def test_the_active_profile_carries_its_engine() -> None:
    assert SETTINGS["live_engine"] == "VOICE_LIVE_ENGINE"


class TestResolving:
    def test_an_unknown_engine_is_the_default(self) -> None:
        rows = [
            LiveEngine(llm_id=0, key=seed["key"], transport=seed["transport"])
            for seed in ENGINE_SEEDS
        ]
        found = live_engines.pick(rows, "nope")
        assert found is not None and found.key == live_engines.DEFAULT

    @pytest.mark.asyncio
    async def test_a_model_the_catalog_lacks_waits_for_a_sync(
        self, async_db_session: AsyncSession
    ) -> None:
        assert await live_engines.seed_missing(async_db_session, ENGINE_SEEDS) == 0

    @pytest.mark.asyncio
    async def test_a_turned_off_engine_is_not_on_offer(
        self, async_db_session: AsyncSession
    ) -> None:
        await seed_voice_catalog(async_db_session)
        off = tuple(
            {**seed, "is_enabled": seed["key"] != "gpt-realtime-2.1"}
            for seed in ENGINE_SEEDS
        )
        await live_engines.seed_missing(async_db_session, off)

        found = await live_engines.resolve(async_db_session, "gpt-realtime-2.1")
        assert found is not None and found.key == live_engines.DEFAULT
