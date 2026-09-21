"""Telling the conversation what you approved.

She proposed a contact and said she would file the statement and link
the bank "once the organization proposal is approved". It was approved,
and she never heard: nothing sent an approval back to the conversation
that asked for it, so the next move needed a person to retype an id
they already had.

Two rules shape it. One message per approval ACTION, not per card, so a
batch of six filings is a sentence rather than six turns. And only
cards that came FROM a conversation are announced: a payee renamed by
hand in the register is not a reply to anything.

The message is sent AFTER the write has landed. She reads it, calls
parties(), and the row has to be there.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from app.core.log import logger

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

Send = Callable[[str, str], Awaitable[None]]

# Result keys that are an id worth quoting back. She needs them to
# propose the next thing without anybody retyping one.
ID_KEYS = (
    "party_id",
    "account_id",
    "document_id",
    "policy_id",
    "claim_id",
    "request_id",
    "stream_id",
    "item_id",
    "fact_id",
    "transaction_id",
    "institution_id",
)


def said(row: Any) -> str:
    """One approved card, as a line.

    Quotes the FROZEN DISPLAY rather than the payload: the display is
    what you saw when you approved, while the payload is ids - and a
    line reading "party 5" where the card said a name is a line nobody
    can check against their memory of saying yes.
    """
    from app.services.finance.domains.writes.registry import executor_for

    try:
        title = executor_for(row.change_type).title
    except Exception:  # noqa: BLE001 - a type that has since been retired
        title = row.change_type
    result = row.result or {}
    subject = next(
        (
            str(one.get("value"))
            for one in result.get("display") or []
            if one.get("value")
        ),
        "",
    )
    ids = ", ".join(
        f"{key} {result[key]}" for key in ID_KEYS if result.get(key) is not None
    )
    line = title
    if subject:
        line += f" - {subject}"
    if ids:
        line += f" ({ids})"
    return line


async def announce_approval(rows: list[Any], *, send: Send) -> None:
    """Tell each conversation what it got, once."""
    by_conversation: dict[str, list[Any]] = {}
    for row in rows:
        if getattr(row, "conversation_id", None):
            by_conversation.setdefault(row.conversation_id, []).append(row)
    for conversation_id, approved in by_conversation.items():
        lines = [said(row) for row in approved]
        # One card is a sentence; several are a list, because a reader
        # scanning six filings wants them under each other.
        message = (
            f"I approved: {lines[0]}."
            if len(lines) == 1
            else "I approved these:\n" + "\n".join(f"- {one}" for one in lines)
        )
        try:
            await send(conversation_id, message)
        except Exception:
            # An approval that landed must not read as failed because
            # telling somebody about it did.
            logger.exception("Could not announce approval to %s", conversation_id)


async def on_the_worker(db: AsyncSession, conversation_id: str, message: str) -> None:
    """The real sender: a turn, queued. Running a model call inside the
    approve request would make Approve sit waiting on one.

    ``db`` is the REQUEST's session, never a fresh one. Touching the
    approved row after its commit had re-opened a transaction on that
    session - the write lock - and a second session here waited on it:
    one request deadlocked on itself for the whole busy timeout, and
    every other request queued behind it (2026-09-21). The lookup is
    released before the queue is touched.
    """
    from app.components.worker.pools import get_queue_pool
    from app.models.conversation import Conversation

    conversation = await db.get(Conversation, conversation_id)
    user_id = conversation.user_id if conversation else "0"
    await db.commit()
    pool, queue_name = await get_queue_pool("system")
    await pool.enqueue_job(
        "announce_approval_task",
        conversation_id,
        message,
        user_id,
        _queue_name=queue_name,
    )


async def announce(db: AsyncSession, rows: list[Any]) -> None:
    """Announce an approval to whoever asked for it. Never raises: an
    approval that landed must not read as failed because telling
    somebody about it did."""
    from functools import partial

    try:
        await announce_approval(rows, send=partial(on_the_worker, db))
    except Exception:
        logger.exception("Could not announce an approval")
