"""Money reaches the model in dollars as well as cents (#291).

Her tools keep integer cents (scripts, cards and proposals compute with
them), and a live call's model read an unlabelled 297614 as dollars and
put a -149203 cents net at -$149. So what a run_code result hands the
model carries each ``*_cents`` figure's dollars beside it, ready to say.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai.messages import ToolCallPart, ToolReturn
from pydantic_ai.tools import ToolDefinition
import pytest

from app.core.formatting import with_dollars
from app.services.ai.domains.chat.agent_loader import _tool_output_limits


class TestWithDollars:
    def test_every_cents_figure_gets_its_dollars_beside_it(self) -> None:
        result = with_dollars(
            {
                "spent_cents": 297_614,
                "months": [{"month": "2026-09", "net_cents": -149_203}],
                "count": 3,
                "label": "x_cents",
            }
        )
        assert result["spent_cents"] == 297_614  # kept for her next script
        assert result["spent_usd"] == "$2,976.14"
        assert result["months"][0]["net_usd"] == "-$1,492.03"
        assert "count_usd" not in result and result["label"] == "x_cents"

    def test_only_whole_numbers_of_cents_are_money(self) -> None:
        assert with_dollars({"flag_cents": True, "odd_cents": "12"}) == {
            "flag_cents": True,
            "odd_cents": "12",
        }

    def test_printed_output_passes_through(self) -> None:
        assert with_dollars("net_cents: -149203") == "net_cents: -149203"


async def _returned(tool: str, result: Any) -> Any:
    """``result`` as a code-mode agent's hook hands it on from ``tool``."""
    return await _tool_output_limits(code_mode=True).after_tool_execute(
        None,
        call=ToolCallPart(tool_name=tool, args={}, tool_call_id="c1"),
        tool_def=ToolDefinition(name=tool),
        args={},
        result=result,
    )


class TestTheModelSeesDollars:
    @pytest.mark.asyncio
    async def test_a_run_code_result_carries_dollars(self) -> None:
        returned = ToolReturn(return_value={"limits": [{"spent_cents": 88_202}]})

        out = await _returned("run_code", returned)

        value = out.return_value if isinstance(out, ToolReturn) else out
        assert value["limits"][0]["spent_usd"] == "$882.02"

    @pytest.mark.asyncio
    async def test_a_script_gets_the_tools_payload_untouched(self) -> None:
        """In code mode only run_code's result is rewritten: a helper's
        payload goes to the script exactly as the tool built it."""
        assert await _returned("budget", {"spent_cents": 88_202}) == {
            "spent_cents": 88_202
        }
