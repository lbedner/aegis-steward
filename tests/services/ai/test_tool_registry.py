"""Tests for the agent tool registry (name -> callable resolution).

``resolve_tools`` hands back instrumented callables, not the registered
objects themselves: every call is wrapped for the tool-call ledger on the
way out. So resolution is asserted on what the wrapper wraps, which is also
what the model sees as the tool.
"""

from collections.abc import Callable, Generator
import inspect
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.services.ai.domains.chat.tools import (
    register_tool,
    registered_tool_names,
    resolve_tools,
    unregister_tool,
)


def unwrapped(tools: list[Callable[..., Any]]) -> list[Callable[..., Any]]:
    """The callables under the ledger wrapper."""
    return [inspect.unwrap(tool) for tool in tools]


@pytest.fixture(autouse=True)
def clean_registry() -> Generator[None]:
    """Remove tools registered by a test so cases stay independent."""
    before = set(registered_tool_names())
    yield
    for name in set(registered_tool_names()) - before:
        unregister_tool(name)


def _echo(text: str) -> str:
    """Echo the given text back."""
    return text


class TestRegistration:
    def test_register_and_resolve(self) -> None:
        register_tool("echo", _echo, description="Echo a string")

        assert "echo" in registered_tool_names()
        assert unwrapped(resolve_tools(["echo"])) == [_echo]

    def test_duplicate_registration_is_an_error(self) -> None:
        register_tool("echo", _echo)

        with pytest.raises(ValueError, match="already registered"):
            register_tool("echo", _echo)

    def test_replace_allows_rebinding(self) -> None:
        register_tool("echo", _echo)

        def other(text: str) -> str:
            """Alternative echo."""
            return text.upper()

        register_tool("echo", other, replace=True)
        assert unwrapped(resolve_tools(["echo"])) == [other]

    @pytest.mark.asyncio
    async def test_a_script_reads_the_money_the_model_is_shown(self) -> None:
        """#460: the model is shown each ``*_cents`` with its ``*_usd``; a
        script calling the same tool got cents alone, read ``amount_usd``
        off a bill, found None, and she said no bill was $250."""

        async def bill() -> dict[str, Any]:
            """One bill."""
            return {"bills": [{"amount_cents": 25_000}]}

        def count() -> dict[str, Any]:
            """A count."""
            return {"spent_cents": 1_920}

        register_tool("bill", bill)
        register_tool("count", count)
        resolved_bill, resolved_count = resolve_tools(["bill", "count"])

        assert (await resolved_bill())["bills"][0]["amount_usd"] == "$250.00"
        assert resolved_count()["spent_usd"] == "$19.20"

    def test_unregister_unknown_is_an_error(self) -> None:
        with pytest.raises(KeyError):
            unregister_tool("never-registered")


class TestResolution:
    def test_unknown_name_is_skipped_with_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A DB row naming a missing callable degrades, never crashes."""
        import app.services.ai.domains.chat.tools as tools_module

        warned = MagicMock()
        monkeypatch.setattr(tools_module.logger, "warning", warned)
        register_tool("echo", _echo)

        resolved = resolve_tools(["echo", "missing-tool"])

        assert unwrapped(resolved) == [_echo]
        # The skip must be surfaced: a silently ignored tool row would be
        # undebuggable.
        warned.assert_called_once()
        assert warned.call_args.kwargs.get("tool_name") == "missing-tool"

    def test_resolution_preserves_order(self) -> None:
        def first(text: str) -> str:
            """First tool."""
            return text

        def second(text: str) -> str:
            """Second tool."""
            return text

        register_tool("second", second)
        register_tool("first", first)

        assert unwrapped(resolve_tools(["first", "second"])) == [first, second]
