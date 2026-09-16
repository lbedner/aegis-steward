"""The memory tools the model calls: save, replace, update, forget.

Storage and injection live in ``user_memory``; this module is the tool
surface over it. Importing it registers the tools.
"""

from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.log import logger
from app.services.ai.domains.chat import user_memory
from app.services.ai.domains.chat.tools import register_tool


async def save_memory(new_fact: str, category: str = "general") -> str:
    """Remember one durable fact about the current user.

    Category is one of: family, food, lifestyle, health, personal,
    program, general.
    """
    user_id = user_memory.current_user_id.get()
    if not user_id:
        logger.warning("save_memory called without user context; nothing saved")
        return "No user context available; nothing was saved."
    if not new_fact.strip():
        return "Nothing to save; provide a fact."
    return await user_memory.own_write(
        lambda session: user_memory.save_user_fact(
            user_id, new_fact, category, session=session
        )
    )


async def replace_memory(memory_text: str) -> str:
    """Replace everything known about the current user, one fact per line."""
    user_id = user_memory.current_user_id.get()
    if not user_id:
        logger.warning("replace_memory called without user context; nothing saved")
        return "No user context available; nothing was saved."
    return await user_memory.own_write(
        lambda session: user_memory.replace_user_memory(
            user_id, memory_text, session=session
        )
    )


def _find_fact(facts: list[dict[str, Any]], words: str) -> int | None:
    """The one saved fact ``words`` picks out, or None. A quote of the fact
    (either direction, case-insensitive) is enough; two matches is a
    question back to the model rather than a guess."""
    hits = [
        i
        for i, entry in enumerate(facts)
        if user_memory._is_duplicate(str(entry.get("fact", "")), words)
    ]
    return hits[0] if len(hits) == 1 else None


async def update_memory(old_fact: str, new_fact: str) -> str:
    """Rewrite one saved fact that has changed - Marisa's hours firmed up,
    a valuation was revised - in place, instead of saving a second one
    beside the first. ``old_fact`` is a quote of the saved fact."""
    user_id = user_memory.current_user_id.get()
    if not user_id:
        return "No user context available; nothing was changed."
    if not new_fact.strip():
        return "Nothing to save; provide the corrected fact."

    async def write(session: AsyncSession) -> str:
        facts = await user_memory.list_user_facts(user_id, session=session)
        index = _find_fact(facts, old_fact)
        if index is None:
            return (
                "No single saved fact matches that; quote it more exactly, or "
                "save_memory if it is new."
            )
        entry = await user_memory.update_user_fact(
            user_id, index, fact=new_fact, session=session
        )
        return f"Updated ({entry['category']}): {entry['fact']}"

    return await user_memory.own_write(write)


async def forget_memory(fact: str) -> str:
    """Drop one saved fact - because it is no longer true, or because a
    tool can now read it (a stream that was declared, an account that
    was created). ``fact`` is a quote of the saved fact."""
    user_id = user_memory.current_user_id.get()
    if not user_id:
        return "No user context available; nothing was forgotten."

    async def write(session: AsyncSession) -> str:
        facts = await user_memory.list_user_facts(user_id, session=session)
        index = _find_fact(facts, fact)
        if index is None:
            return "No single saved fact matches that; quote it more exactly."
        gone = facts[index]["fact"]
        await user_memory.delete_user_fact(user_id, index, session=session)
        return f"Forgot: {gone}"

    return await user_memory.own_write(write)


# Built-in registration: importing this module makes the tools grantable
# via the agent registry. replace=True keeps re-imports idempotent.
register_tool(
    "save_memory",
    save_memory,
    description="Persist one durable fact about the current user",
    native_write=True,
    replace=True,
)
register_tool(
    "replace_memory",
    replace_memory,
    description="Replace all saved facts about the current user",
    native_write=True,
    replace=True,
)
register_tool(
    "update_memory",
    update_memory,
    description="Rewrite one saved fact that has changed, in place",
    native_write=True,
    replace=True,
)
register_tool(
    "forget_memory",
    forget_memory,
    description="Drop one saved fact that is no longer true or that a tool can now read",
    native_write=True,
    replace=True,
)
