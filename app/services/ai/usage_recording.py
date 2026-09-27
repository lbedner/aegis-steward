"""LLM usage extraction, pricing, and ledger writes — module-level.

Extracted from ``AIService`` so non-chat callers (the insight generation
pipeline, future one-shot agents) can price and record LLM calls without
instantiating the chat service (whose ``ConversationManager`` runs
``init_database()`` on construction — a ``create_all`` that must never race
Alembic in worker processes). ``AIService`` delegates here; the price
lookup lives in exactly one place.
"""

from datetime import UTC, datetime
from typing import Any

from sqlmodel import select

from app.core.db import get_async_session
from app.core.log import logger

from .models.llm import LargeLanguageModel, LLMPrice, LLMUsage


def _bare_model_name(model_name: str) -> str:
    """Strip a vendor prefix ("openai/gpt-4o" → "gpt-4o")."""
    if "/" in model_name:
        return model_name.split("/", 1)[1]
    return model_name


def _token_count(usage: Any, *names: str) -> int:
    """First integer attribute among ``names``, else 0 (mock-safe)."""
    for name in names:
        value = getattr(usage, name, None)
        if isinstance(value, int):
            return value
    return 0


def extract_usage(result: Any) -> dict[str, int]:
    """Token usage from a pydantic-ai run result.

    Handles every shape pydantic-ai has shipped: ``result.usage`` as a
    data attribute (legacy ``Usage`` with request/response_tokens) or as
    a method returning ``RunUsage`` (input/output_tokens). Anything
    unreadable degrades to zeros rather than raising — usage tracking
    must never fail the request.

    Cache accounting: pydantic-ai's ``input_tokens`` AGGREGATES uncached +
    cache-read + cache-write tokens, so the cache splits ride along for
    pricing (cached reads bill at ~0.1x, writes at ~1.25x on Anthropic).
    Zero on providers/runs without caching, which prices identically to
    the pre-cache behavior.
    """
    usage = getattr(result, "usage", None)
    if callable(usage):
        try:
            usage = usage()
        except Exception:
            return {"input_tokens": 0, "output_tokens": 0}
    if usage is None:
        return {"input_tokens": 0, "output_tokens": 0}
    return {
        "input_tokens": _token_count(usage, "input_tokens", "request_tokens"),
        "output_tokens": _token_count(usage, "output_tokens", "response_tokens"),
        "cache_read_tokens": _token_count(usage, "cache_read_tokens"),
        "cache_write_tokens": _token_count(usage, "cache_write_tokens"),
    }


async def _latest_price(session: Any, model_name: str) -> LLMPrice | None:
    """Current price row for a bare model name, or None if uncataloged."""
    llm = (
        await session.exec(
            select(LargeLanguageModel).where(LargeLanguageModel.model_id == model_name)
        )
    ).first()
    if not llm:
        return None
    return (
        await session.exec(
            select(LLMPrice)
            .where(LLMPrice.llm_id == llm.id)
            .order_by(LLMPrice.effective_date.desc())
        )
    ).first()


async def calculate_cost(
    model_name: str, input_tokens: int, output_tokens: int
) -> float:
    """Full-rate cost estimate in USD, 0.0 when model/price is uncataloged.

    This charges every input token at the base rate; it takes aggregate counts
    and has no cache split to price reads/writes differently. It's a display
    estimate (e.g. a live status line). ``record_usage`` is the authoritative,
    cache-aware path that writes the ledger, so for cached runs the ledgered
    cost is lower than this estimate.
    """
    bare = _bare_model_name(model_name)
    try:
        async with get_async_session() as session:
            price = await _latest_price(session, bare)
            if not price:
                return 0.0
            return (
                input_tokens * price.input_cost_per_token
                + output_tokens * price.output_cost_per_token
            )
    except Exception as e:
        logger.warning("Failed to calculate cost", error=str(e), model=bare)
        return 0.0


# Anthropic's cache multipliers on the input price: reads bill at ~0.1x,
# writes (5m TTL) at 1.25x. Other providers differ, but cache token counts
# only flow for runs where pydantic-ai reports them; zero = old pricing.
_CACHE_READ_MULT = 0.1
_CACHE_WRITE_MULT = 1.25


def _priced_input_cost(usage: dict[str, int], input_price: float) -> float:
    """Input-side cost with cache-aware pricing.

    ``input_tokens`` arrives as pydantic-ai's AGGREGATE (uncached + cache
    reads + cache writes), so the cached share is re-priced at its own
    multiplier instead of full rate. Without cache counts this reduces to
    ``input_tokens * price`` exactly as before.
    """
    total = usage.get("input_tokens", 0)
    reads = usage.get("cache_read_tokens", 0)
    writes = usage.get("cache_write_tokens", 0)
    uncached = max(0, total - reads - writes)
    return (
        uncached * input_price
        + reads * input_price * _CACHE_READ_MULT
        + writes * input_price * _CACHE_WRITE_MULT
    )


async def record_usage(
    action: str,
    model_name: str,
    usage: dict[str, int],
    user_id: str | None,
    success: bool = True,
    error_message: str | None = None,
    duration_ms: int | None = None,
    tool_calls: int | None = None,
    conversation_id: str | None = None,
) -> float:
    """Write one ``llm_usage`` ledger row; returns the calculated cost.

    Unknown models are still recorded (at zero cost) so the ledger stays
    complete; a failed write is logged and swallowed — usage tracking must
    never fail the calling request. The returned cost lets callers
    denormalize it onto their own artifacts without a second price lookup.
    """
    bare = _bare_model_name(model_name)
    total_cost = 0.0

    async def write() -> float:
        # Read the price, then write the row: an upgrade that can fail
        # at once under another writer, so it is retried where it happens.
        async with get_async_session() as session:
            price = await _latest_price(session, bare)
            cost = 0.0
            if price:
                cost = (
                    _priced_input_cost(usage, price.input_cost_per_token)
                    + usage.get("output_tokens", 0) * price.output_cost_per_token
                )
            else:
                logger.warning(
                    "LLM not priced in catalog - usage recorded without cost",
                    model_id=bare,
                )
            session.add(
                LLMUsage(
                    action=action,
                    model_id=bare,
                    user_id=user_id,
                    timestamp=datetime.now(UTC),
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    total_cost=cost,
                    success=success,
                    error_message=error_message,
                    duration_ms=duration_ms,
                    tool_calls=tool_calls,
                    conversation_id=conversation_id,
                )
            )
            return cost

    try:
        total_cost = await write()
        logger.info(
            "Usage committed to database",
            model=bare,
            tokens=usage,
            cost=total_cost,
        )
    except Exception as e:
        logger.error("Failed to record LLM usage", error=str(e))
    return total_cost


# --- voice (#270) ------------------------------------------------------------
# Voice bills by the second (a live call, a transcription), by the
# character or second of speech, or by audio tokens (a realtime call).
# Every rate is the catalog's (``llm_price``, synced with the model), so
# a voice cost is priced exactly where a chat turn's is.


def _measured_cost(
    price: LLMPrice | None,
    *,
    input_seconds: float | None = None,
    output_seconds: float | None = None,
    characters: int | None = None,
) -> float:
    """Each measure times the rate the model lists for it. A model lists
    only the measures it bills by, so the others add nothing."""
    if price is None:
        return 0.0
    return (
        (input_seconds or 0) * (price.input_cost_per_second or 0)
        + (output_seconds or 0) * (price.output_cost_per_second or 0)
        + (characters or 0) * (price.input_cost_per_character or 0)
    )


async def speech_cost(
    model: str,
    *,
    input_seconds: float | None = None,
    output_seconds: float | None = None,
    characters: int | None = None,
) -> float:
    """What a transcription or a spoken reply cost; 0 for a model the
    catalog does not price."""
    try:
        async with get_async_session() as session:
            price = await _latest_price(session, _bare_model_name(model))
    except Exception as e:
        logger.warning("Failed to price speech", error=str(e), model=model)
        return 0.0
    return _measured_cost(
        price,
        input_seconds=input_seconds,
        output_seconds=output_seconds,
        characters=characters,
    )


def _realtime_cost(price: LLMPrice | None, usage: Any) -> float:
    """A realtime call's tokens at the catalog's text, audio and cached
    rates. The totals include the audio and the cached share, so each is
    taken out and priced at its own rate."""
    if price is None:
        return 0.0

    def count(name: str) -> int:
        value = getattr(usage, name, 0)
        return value if isinstance(value, int) else 0

    audio_in, audio_out = count("input_audio_tokens"), count("output_audio_tokens")
    cached, cached_audio = count("cache_read_tokens"), count("cache_audio_read_tokens")
    text_in = max(0, count("input_tokens") - audio_in - (cached - cached_audio))
    text_out = max(0, count("output_tokens") - audio_out)
    input_rate = price.input_cost_per_token
    return (
        text_in * input_rate
        + max(0, audio_in - cached_audio)
        * (price.input_cost_per_audio_token or input_rate)
        + cached * (price.cache_input_cost_per_token or input_rate)
        + text_out * price.output_cost_per_token
        + audio_out * (price.output_cost_per_audio_token or price.output_cost_per_token)
    )


async def record_speech(
    action: str,
    model: str,
    *,
    cost: float,
    seconds: float | None = None,
    user_id: str | None = None,
    success: bool = True,
    error_message: str | None = None,
) -> None:
    """One ledger row for a transcription (``stt``) or spoken reply
    (``tts``), beside the model calls. Tokens are 0: speech reports none."""
    try:
        async with get_async_session() as session:
            session.add(
                LLMUsage(
                    action=action,
                    model_id=model,
                    user_id=user_id,
                    timestamp=datetime.now(UTC),
                    input_tokens=0,
                    output_tokens=0,
                    total_cost=cost,
                    audio_seconds=seconds,
                    success=success,
                    error_message=error_message,
                )
            )
    except Exception as e:
        logger.error("Failed to record speech usage", error=str(e))


LIVE_ACTION = "live"
# How a call may end without anything having gone wrong.
_CLEAN_ENDINGS = frozenset({"close_requested", "remote_hangup"})


async def open_live_call(
    session_id: str,
    *,
    model: str,
    conversation_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """The call's ledger row, opened with the call and updated as it runs."""
    try:
        async with get_async_session() as session:
            session.add(
                LLMUsage(
                    action=LIVE_ACTION,
                    model_id=model,
                    user_id=user_id,
                    timestamp=datetime.now(UTC),
                    input_tokens=0,
                    output_tokens=0,
                    total_cost=0.0,
                    audio_seconds=0.0,
                    session_id=session_id,
                    conversation_id=conversation_id,
                )
            )
    except Exception as e:
        logger.error("Failed to open a live call's usage", error=str(e))


async def live_call_seconds(
    session_id: str, seconds: float, *, reason: str | None = None
) -> bool:
    """The call's billed seconds so far - a running total the provider
    reports, never summed - and, at the end, how it ended. Returns whether
    the call was known."""
    async with get_async_session() as session:
        row = (
            await session.exec(
                select(LLMUsage).where(
                    LLMUsage.session_id == session_id,
                    LLMUsage.action == LIVE_ACTION,
                )
            )
        ).first()
        if row is None:
            return False
        billed = max(row.audio_seconds or 0.0, seconds)
        row.audio_seconds = billed
        row.total_cost = _measured_cost(
            await _latest_price(session, row.model_id), input_seconds=billed
        )
        row.duration_ms = billed * 1000
        if reason is not None:
            row.success = reason in _CLEAN_ENDINGS
            row.error_message = None if row.success else reason
        session.add(row)
    return True


REALTIME_ACTION = "realtime"


async def record_realtime(
    model: str,
    usage: Any,
    *,
    seconds: float,
    conversation_id: str | None,
    user_id: str | None = None,
) -> None:
    """A Pydantic AI realtime call's row (#273): its tokens, priced at the
    catalog's rates like every other model call."""
    try:
        async with get_async_session() as session:
            price = await _latest_price(session, _bare_model_name(model))
            session.add(
                LLMUsage(
                    action=REALTIME_ACTION,
                    model_id=model,
                    user_id=user_id,
                    timestamp=datetime.now(UTC),
                    input_tokens=usage.input_tokens or 0,
                    output_tokens=usage.output_tokens or 0,
                    total_cost=_realtime_cost(price, usage),
                    audio_seconds=seconds,
                    duration_ms=seconds * 1000,
                    tool_calls=getattr(usage, "tool_calls", None),
                    conversation_id=conversation_id,
                )
            )
    except Exception as e:
        logger.error("Failed to record a realtime call's usage", error=str(e))
