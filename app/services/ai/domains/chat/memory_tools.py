"""The memory tools the model calls: save, replace, update, forget.

Storage and injection live in ``user_memory``; this module is the tool
surface over it. Each tool files a card (``memory_changes``). Importing it
registers the tools.
"""

from typing import Any

from app.core.log import logger
from app.services.ai.domains.chat import user_memory
from app.services.ai.domains.chat.tools import register_tool


async def save_memory(new_fact: str, category: str = "general") -> dict[str, Any] | str:
    """Ask the user to approve remembering one durable fact about them.

    Category is one of: {categories}. This files an approval card: nothing
    is remembered until the user approves it, so never say it is saved.
    """
    user_id = user_memory.current_user_id.get()
    if not user_id:
        logger.warning("save_memory called without user context; nothing saved")
        return "No user context available; nothing was saved."
    if not new_fact.strip():
        return "Nothing to save; provide a fact."
    return await _propose(
        "memory.save", {"user_id": user_id, "fact": new_fact, "category": category}
    )


save_memory.__doc__ = (save_memory.__doc__ or "").format(
    categories=", ".join(user_memory.MEMORY_CATEGORIES)
)


async def replace_memory(memory_text: str) -> dict[str, Any] | str:
    """Ask the user to approve replacing everything known about them, one
    fact per line. Files an approval card: nothing changes until the user
    approves it."""
    user_id = user_memory.current_user_id.get()
    if not user_id:
        logger.warning("replace_memory called without user context; nothing saved")
        return "No user context available; nothing was saved."
    return await _propose(
        "memory.replace", {"user_id": user_id, "memory_text": memory_text}
    )


async def _propose(change_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """File the card; why, see ``memory_changes``."""
    from app.services.finance.ai_write_tools import propose

    return await propose(change_type, payload)


async def update_memory(old_fact: str, new_fact: str) -> dict[str, Any] | str:
    """Ask the user to approve rewriting one saved fact that has changed -
    the hours firmed up, a valuation was revised - in place, instead of
    saving a second one beside the first. ``old_fact`` is a quote of the
    saved fact. Files an approval card: nothing changes until the user
    approves it."""
    user_id = user_memory.current_user_id.get()
    if not user_id:
        return "No user context available; nothing was changed."
    if not new_fact.strip():
        return "Nothing to save; provide the corrected fact."
    # Checked here, not left to the card: a card whose fact is gone still
    # files (it draws as "No longer applies").
    try:
        saved = await user_memory.saved_fact(user_id, old_fact)
    except ValueError as e:
        return f"{e} If it is new, save_memory it."
    return await _propose(
        "memory.update",
        {"user_id": user_id, "old_fact": saved["fact"], "new_fact": new_fact},
    )


async def forget_memory(fact: str) -> dict[str, Any] | str:
    """Ask the user to approve dropping one saved fact - because it is no
    longer true, or because a tool can now read it (a stream that was
    declared, an account that was created). ``fact`` is a quote of the
    saved fact. Files an approval card: nothing is forgotten until the
    user approves it, so never say it is gone."""
    user_id = user_memory.current_user_id.get()
    if not user_id:
        return "No user context available; nothing was forgotten."
    try:  # as in update_memory
        saved = await user_memory.saved_fact(user_id, fact)
    except ValueError as e:
        return str(e)
    return await _propose("memory.forget", {"user_id": user_id, "fact": saved["fact"]})


# Built-in registration: importing this module makes the tools grantable
# via the agent registry. replace=True keeps re-imports idempotent.
register_tool(
    "save_memory",
    save_memory,
    description="File a card asking to remember one durable fact about the user",
    native_write=True,
    replace=True,
)
register_tool(
    "replace_memory",
    replace_memory,
    description="File a card asking to replace all saved facts about the user",
    native_write=True,
    replace=True,
)
register_tool(
    "update_memory",
    update_memory,
    description="File a card asking to rewrite one saved fact that has changed",
    native_write=True,
    replace=True,
)
register_tool(
    "forget_memory",
    forget_memory,
    description="File a card asking to drop one saved fact no longer true or now readable",
    native_write=True,
    replace=True,
)
