"""Her live engines, as table data (#273).

A live call runs on an engine (``models/live_engine.py``): a catalog model
and how she uses it. ``gpt_live`` is our own GPT-Live path
(``chat_live.py``): full duplex, it hums and backchannels, and hands the
thinking to her agent. ``realtime`` is a Pydantic AI realtime model that
IS the brain, running her agent's prompt, tools and code mode, and takes
turns. The chip, the profile form and the phone all read these rows; the
defaults are seeded when missing.
"""

from __future__ import annotations

from typing import Any, Literal

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.log import logger
from app.services.ai.domains.llm.queries import latest_prices_by_llm_ids
from app.services.ai.domains.voice import queries
from app.services.ai.models.live_engine import LiveEngine

Transport = Literal["gpt_live", "realtime", "relay"]
DEFAULT = "gpt-live"
# Whose realtime models a browser call can reach, and how. Pydantic AI
# answers the browser's WebRTC offer for OpenAI, so audio goes straight
# there ("realtime"); Gemini Live speaks only a WebSocket holding the key,
# so our server carries the audio both ways ("relay", chat_live.py).
CALL_TRANSPORTS: dict[str, Transport] = {"openai": "realtime", "google": "relay"}

enabled = queries.enabled_engines


def pick(engines: list[LiveEngine], key: str | None) -> LiveEngine | None:
    """The engine ``key`` names among ``engines``, else the default."""
    keyed = {engine.key: engine for engine in engines}
    return keyed.get(key or DEFAULT) or keyed.get(DEFAULT)


async def resolve(session: AsyncSession, key: str | None) -> LiveEngine | None:
    """The enabled engine ``key`` names, or the default when it is gone or off."""
    return pick(await enabled(session), key)


def realtime_model(engine: LiveEngine) -> str:
    """The Pydantic AI model string: the serving org, then the model."""
    # ponytail: the catalog org slug doubles as Pydantic AI's provider
    # prefix ("openai"); a provider whose names differ needs a mapping.
    return f"{engine.llm.served_by.slug}:{engine.llm.model_id}"


async def per_second(session: AsyncSession, engine: LiveEngine) -> float | None:
    """USD a second when the engine's model bills by time (the catalog's
    rate); None when it is priced from its tokens after the call."""
    prices = await latest_prices_by_llm_ids(session, [engine.llm_id])
    price = prices.get(engine.llm_id)
    return price.input_cost_per_second if price else None


async def choose(
    session: AsyncSession, model_id: str, seeds: tuple[dict[str, Any], ...]
) -> LiveEngine | None:
    """The engine for a catalog model picked for calls, offered again if
    it was turned off, or made on the spot: the seeded engine of its
    transport lends its instructions and reply cap, so a new pick talks
    like a call at once and is tuned on its row afterwards. None when the
    catalog lacks the model or no call can reach its vendor."""
    engine = await queries.engine_for_model(session, model_id)
    if engine is None:
        found = await queries.model_vendor(session, model_id)
        if found is None or found[1] not in CALL_TRANSPORTS:
            return None
        llm_id, vendor = found
        # GPT-Live is its own API (our client delegation); every other
        # realtime model runs her agent itself, by its vendor's transport.
        transport = (
            "gpt_live" if model_id.startswith("gpt-live") else CALL_TRANSPORTS[vendor]
        )
        template = next(seed for seed in seeds if seed["transport"] == transport)
        engine = LiveEngine(
            key=model_id,
            llm_id=llm_id,
            transport=transport,
            instructions=template.get("instructions"),
            max_output_tokens=template.get("max_output_tokens"),
            sort_order=len(seeds),
        )
    engine.is_enabled = True
    session.add(engine)
    await session.commit()
    return engine


async def seed_missing(session: AsyncSession, seeds: tuple[dict[str, Any], ...]) -> int:
    """Insert the engines not yet in the table; never touch one that is.
    A seed names its model by catalog id; one the catalog lacks is
    skipped until a sync brings it. Returns how many were added."""
    have = await queries.engine_keys(session)
    wanted = [seed for seed in seeds if seed["key"] not in have]
    if not wanted:
        return 0
    ids = await queries.catalog_ids(session, [seed["model"] for seed in wanted])
    added = []
    for seed in wanted:
        values = {k: v for k, v in seed.items() if k != "model"}
        if seed["model"] not in ids:
            logger.warning(
                "Live engine's model is not in the catalog yet",
                engine=seed["key"],
                model=seed["model"],
            )
            continue
        added.append(LiveEngine(llm_id=ids[seed["model"]], **values))
    session.add_all(added)
    if added:
        await session.commit()
    return len(added)
