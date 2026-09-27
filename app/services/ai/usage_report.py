"""What models and voice cost, read back from the one ledger (#270).

``llm_usage`` holds chat and agent turns, live calls (``live``),
transcriptions (``stt``) and spoken replies (``tts``). The report groups
it into those four kinds, lists it by model, and costs each live call as
its minutes plus the answers her agent gave during it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.ai.domains.llm import queries
from app.services.ai.usage_recording import LIVE_ACTION

KIND_LABELS = {
    "chat": "Chat and agents",
    "live": "Live calls",
    "stt": "Hearing",
    "tts": "Speaking",
}
# An answer belongs to a call if it lands while the call runs; a reply
# still arriving as it is hung up counts too.
_CALL_GRACE = timedelta(minutes=2)
_CALL_LIMIT = 50


def kind_of(action: str) -> str:
    return action if action in ("live", "stt", "tts") else "chat"


async def usage_report(
    session: AsyncSession, *, days: int, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    # Stored naive in UTC; compare the same way.
    since = (now - timedelta(days=days)).astimezone(UTC).replace(tzinfo=None)

    grouped = await queries.usage_by_action_and_model(session, since)
    kinds = {
        key: {"key": key, "label": label, "cost": 0.0, "uses": 0, "minutes": 0.0}
        for key, label in KIND_LABELS.items()
    }
    models: dict[tuple[str, str], dict[str, Any]] = {}
    for row in grouped:
        kind = kind_of(row.action)
        kinds[kind]["cost"] += row.cost
        kinds[kind]["uses"] += row.uses
        kinds[kind]["minutes"] += row.seconds / 60
        model = models.setdefault(
            (kind, row.model_id),
            {
                "kind": KIND_LABELS[kind],
                "model": row.model_id,
                "uses": 0,
                "minutes": 0.0,
                "tokens": 0,
                "cost": 0.0,
            },
        )
        model["uses"] += row.uses
        model["minutes"] += row.seconds / 60
        model["tokens"] += row.tokens
        model["cost"] += row.cost

    calls = await queries.live_call_rows(session, since, LIVE_ACTION, _CALL_LIMIT)
    turns = await queries.turns_in_conversations(
        session,
        {c.conversation_id for c in calls if c.conversation_id},
        since,
        LIVE_ACTION,
    )
    call_rows = []
    for call in calls:
        ends = call.timestamp + timedelta(seconds=call.audio_seconds or 0) + _CALL_GRACE
        answers = sum(
            cost
            for conversation_id, when, cost in turns
            if conversation_id == call.conversation_id
            and call.timestamp <= when <= ends
        )
        call_rows.append(
            {
                "when": call.timestamp,
                "model": call.model_id,
                "minutes": (call.audio_seconds or 0) / 60,
                "call_cost": call.total_cost,
                "answers_cost": answers,
                "total": call.total_cost + answers,
                "ended": call.error_message or ("ok" if call.success else "failed"),
            }
        )
    return {
        "days": days,
        "total": sum(k["cost"] for k in kinds.values()),
        "kinds": list(kinds.values()),
        "models": sorted(models.values(), key=lambda m: m["cost"], reverse=True),
        "calls": call_rows,
    }
