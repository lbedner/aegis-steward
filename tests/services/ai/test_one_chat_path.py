"""A turn is the same turn however it is asked for (#455).

``AIService`` ran a turn two ways: ``stream_chat`` (the chat page, live
voice) kept each tool call in ``tool_trace`` with the model, provider and
cost; ``chat`` (approval announcements, the API's non-streaming endpoint,
the CLI) kept only text and tokens. Every "I approved: ..." turn lost what
she did, a card proposed there could not be drawn, and her next turn lost
the ids the trace's steps line carried.
"""

from collections.abc import AsyncIterator
import json
from typing import Any

from pydantic_ai.models.function import DeltaToolCall, DeltaToolCalls, FunctionModel
import pytest

from app.core.config import settings
from app.services.ai.service import AIService

OWNER = "0"


pytestmark = pytest.mark.usefixtures("clean_agent_cache")


@pytest.mark.asyncio
async def test_a_turn_through_chat_keeps_what_she_did(
    finance_agent: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    offered: list[str] = []

    async def _stream(messages: Any, info: Any) -> AsyncIterator[str | DeltaToolCalls]:
        offered[:] = [t.name for t in info.function_tools]
        called = any(
            getattr(part, "part_kind", "") == "tool-return"
            for message in messages
            for part in getattr(message, "parts", [])
        )
        if not called:
            # Code mode: her tools are called from inside one script.
            yield {
                0: DeltaToolCall(
                    name="run_code",
                    json_args=json.dumps({"code": "print(6 * 7)"}),
                    tool_call_id="c1",
                )
            }
        else:
            yield "42."

    # Pinned to Ollama and only its model swapped, as the voice turn test
    # does: the agent itself (prompt, tools, grants) is built the real way.
    monkeypatch.setattr(settings, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(
        "app.services.ai.domains.llm.agents._ollama_model",
        lambda config, settings: FunctionModel(stream_function=_stream),
    )
    reply = await AIService(settings).chat(
        "What is six times seven?",
        user_id=OWNER,
        agent_slug=finance_agent,
        surface="finance",
    )

    assert "run_code" in offered, offered
    trace = reply.metadata.get("tool_trace") or []
    assert [step["tool"] for step in trace] == ["run_code"]
    assert "42" in trace[0]["result"]
    assert reply.metadata.get("model") and reply.metadata.get("provider")
