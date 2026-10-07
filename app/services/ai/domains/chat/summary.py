"""A conversation's running summary: what fell out of the window (#295).

Each turn replays as much history as its budget holds, newest first, and
the oldest turns drop. Without this, what was said only in them was gone:
at 519 messages Illiana said 256 had fallen out of her view and that
anything said there "may need to be repeated". The turns that leave the
window are folded, off the request path (``fold_conversation_task``),
into one running summary kept on the conversation's metadata; the turn's
history builder puts it ahead of the turns still in view.

Folding waits until ``FOLD_AFTER`` messages have left unsummarized, so a
long thread costs one small model call per that many turns, not one per
turn.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.core.db import get_async_session
from app.core.log import logger
from app.models.conversation import Conversation as ConversationModel
from app.services.ai.models import Conversation, MessageRole

SUMMARY_KEY = "summary"
FOLD_AFTER = 20
# Long enough for the names, numbers and decisions of a long thread; the
# summary rides every turn, so it is capped like any replayed history.
SUMMARY_CHARS = 2_000

SUMMARY_PROMPT = (
    "You keep the running summary of a long conversation between a user and "
    "their assistant. You are given the summary so far (it may be empty) and "
    "the turns that have just left the assistant's view. Return the new "
    "summary: everything still worth knowing from both, in plain sentences. "
    "Keep names, amounts, dates, decisions, promises and open questions "
    f"exactly. Drop small talk. Stay under {SUMMARY_CHARS} characters. "
    "Return only the summary."
)


def summary_of(conversation: Conversation) -> dict[str, Any]:
    """The summary so far: ``text``, and ``through``, how many of the
    oldest messages it covers."""
    kept = conversation.metadata.get(SUMMARY_KEY) or {}
    return {"text": kept.get("text", ""), "through": int(kept.get("through", 0))}


def due(conversation: Conversation, first_kept: int) -> int | None:
    """Where to fold up to, when enough has left the window unsummarized
    since the last fold; else None. ``first_kept`` is the index of the
    oldest message this turn still showed."""
    through = summary_of(conversation)["through"]
    return first_kept if first_kept - through >= FOLD_AFTER else None


def _transcript(conversation: Conversation, start: int, end: int) -> str:
    lines = []
    for message in conversation.messages[start:end]:
        if message.role == MessageRole.USER:
            lines.append(f"User: {message.content}")
        elif message.role == MessageRole.ASSISTANT:
            lines.append(f"Assistant: {message.content}")
    return "\n".join(lines)


async def _condense(prompt: str, *, conversation: Conversation) -> str:
    """One model call: the folded summary. Its usage is ledgered against
    the conversation like any of its turns."""
    from app.core.config import settings
    from app.services.ai.config import AIServiceConfig
    from app.services.ai.domains.llm.providers import get_agent
    from app.services.ai.usage_recording import record_usage

    config = AIServiceConfig.from_settings(settings)
    result = await get_agent(config, settings, SUMMARY_PROMPT).run(prompt)
    usage = result.usage
    await record_usage(
        "chat:summary",
        config.model,
        {
            "input_tokens": usage.input_tokens or 0,
            "output_tokens": usage.output_tokens or 0,
        },
        conversation.user_id,
        conversation_id=conversation.id,
    )
    return str(result.output).strip()[:SUMMARY_CHARS]


async def fold(conversation: Conversation, *, upto: int) -> bool:
    """Fold the messages up to ``upto`` into the conversation's summary.
    Safe to repeat: a fold already done costs nothing. True when the
    summary changed; the caller saves the conversation."""
    so_far = summary_of(conversation)
    if upto <= so_far["through"]:
        return False
    fallen = _transcript(conversation, so_far["through"], upto)
    prompt = f"Summary so far:\n{so_far['text'] or '(none)'}\n\nTurns that left view:\n{fallen}"
    text = await _condense(prompt, conversation=conversation)
    conversation.metadata[SUMMARY_KEY] = {"text": text, "through": upto}
    return True


async def get_queue_pool(queue: str) -> tuple[Any, str]:
    """The worker queue, imported when needed: a stack without a worker
    imports this module and simply never folds."""
    from app.components.worker.pools import get_queue_pool as pool_for

    return await pool_for(queue)


async def enqueue_if_due(conversation: Conversation, first_kept: int) -> None:
    """After a turn: hand a due fold to the worker. Never fails the turn -
    a fold missed now is due again on the next.

    One per conversation at a time: the summary does not move until a
    fold finishes, so without a fixed job id every turn while one ran
    queued another (#437). arq refuses the id while the job is queued or
    running, and while its result is kept; the next fold due then takes
    in everything that fell out meanwhile."""
    upto = due(conversation, first_kept)
    if upto is None:
        return
    try:
        pool, queue_name = await get_queue_pool("system")
        await pool.enqueue_job(
            "fold_conversation_task",
            conversation.id,
            upto,
            _job_id=f"fold:{conversation.id}",
            _queue_name=queue_name,
        )
    except Exception:
        logger.exception("Enqueueing a conversation fold failed")


def merged_summary(
    stored: dict[str, Any] | None, saving: dict[str, Any] | None
) -> dict[str, Any] | None:
    """The summary a conversation's save keeps: a turn that read the
    conversation before a fold landed must not put back the older one."""
    if not stored or not saving:
        return saving or stored
    if int(stored.get("through", 0)) > int(saving.get("through", 0)):
        return stored
    return saving


async def _write_summary(
    conversation_id: str, change: Callable[[dict[str, Any]], dict[str, Any]]
) -> bool:
    """Write ONLY the summary into the stored metadata as it is now: a
    turn may have landed meanwhile, and saving a copy read earlier would
    undo what it changed. ``change`` gets the stored summary."""
    async with get_async_session() as session:
        row = await session.get(ConversationModel, conversation_id)
        if row is None:
            return False
        meta = row.meta_data or {}
        row.meta_data = {**meta, SUMMARY_KEY: change(meta.get(SUMMARY_KEY) or {})}
        session.add(row)
        await session.commit()
    return True


async def fold_conversation(conversation_id: str, upto: int) -> bool:
    """The worker's fold: read the conversation, condense holding no
    session, then write only the summary (``_write_summary``)."""
    from app.services.ai.deps import ai_service

    conversation = await ai_service.get_conversation(conversation_id)
    if conversation is None or not await fold(conversation, upto=upto):
        return False
    folded = conversation.metadata[SUMMARY_KEY]
    return await _write_summary(conversation_id, lambda _stored: folded)
