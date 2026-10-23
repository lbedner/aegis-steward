"""An agent's tools: its ``agent_tool`` rows' names, resolved from the
core registry (``app.core.tools``) and wrapped for the tool-call ledger.

A database row naming a tool with no registered callable is skipped with
a warning, never an error: a stale row must not brick chat.
"""

from collections.abc import Iterable
import functools
import inspect
from typing import Any

from app.core.log import logger
from app.core.money import with_dollars
from app.core.tools import ADMIN_ONLY, ToolFunc, get_tool
from app.services.ai.domains.chat.tool_telemetry import instrument
from app.services.shared.deps import caller_is_admin


def resolve_tools(names: Iterable[str]) -> list[ToolFunc]:
    """Resolve tool names to callables, preserving order.

    Unknown names are skipped with a warning: a ``tool`` row whose
    callable was renamed or removed degrades that one tool, not the
    whole agent.

    Every callable is wrapped for the per-call ledger on the way out, and
    returns its money in dollars beside the cents. This is the one seam an
    agent's tools all pass through, so a new tool is measured - and read -
    by existing rather than by remembering a decorator. An ``admin`` tool
    answers only an admin (``_admins_only``).
    """
    resolved: list[ToolFunc] = []
    for name in names:
        entry = get_tool(name)
        if entry is None:
            logger.warning("Tool has no registered callable; skipping", tool_name=name)
            continue
        func = _admins_only(entry.func) if entry.admin else entry.func
        resolved.append(instrument(entry.name, _in_dollars(func)))
    return resolved


def _admins_only(func: ToolFunc) -> ToolFunc:
    """``func`` answering only an admin (``caller_is_admin``), as MCP
    serves an admin tool: an agent granted it serves whoever chats."""

    @functools.wraps(func)  # the schema the model sees is func's
    async def guarded(*args: Any, **kwargs: Any) -> Any:
        if not await caller_is_admin():
            return ADMIN_ONLY
        result = func(*args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    return guarded


def _in_dollars(func: ToolFunc) -> ToolFunc:
    """``func`` with each ``*_cents`` joined by its ``*_usd`` (``with_dollars``),
    so a ``run_code`` script reads what the model is shown (#460)."""
    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def run(*args: Any, **kwargs: Any) -> Any:
            return with_dollars(await func(*args, **kwargs))

        return run

    @functools.wraps(func)
    def run_sync(*args: Any, **kwargs: Any) -> Any:
        return with_dollars(func(*args, **kwargs))

    return run_sync
