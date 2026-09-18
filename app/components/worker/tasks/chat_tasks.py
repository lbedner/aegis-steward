"""Telling a conversation what was approved, as a worker task (arq form).

On the worker rather than in the approve request, because running a
turn is a model call: clicking Approve must not sit waiting on one, and
a slow model must not make the button read as broken.

The body imports the AI service lazily, so a worker stack without it
imports this module and simply never receives the task.
"""

from typing import Any


async def announce_approval_task(
    ctx: dict[str, Any],
    conversation_id: str,
    message: str,
    user_id: str,
) -> dict[str, Any]:
    from app.core.config import settings
    from app.services.ai.service import AIService

    reply = await AIService(settings).chat(
        message, conversation_id=conversation_id, user_id=user_id
    )
    return {"conversation_id": conversation_id, "replied": bool(reply)}
