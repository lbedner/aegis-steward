"""A model that rejects ``temperature`` is learned from, not hard-coded.

``gpt-6.1-sol`` 400s on any temperature ("Unsupported parameter:
'temperature' is not supported with this model"), and the only guard
was a list of name prefixes written for the gpt-5 and o-series
generation, so every chat turn on it failed (2026-09-30). The next such
model will not be on the list either: the turn retries once without it,
and the model is never sent one again.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
import pytest

from app.services.ai.domains.llm import model_factory
from app.services.ai.domains.llm.model_factory import (
    _supports_custom_temperature,
    tolerant,
)

REJECTED = {
    "message": "Unsupported parameter: 'temperature' is not supported with this model.",
    "type": "invalid_request_error",
    "param": "temperature",
}


def _refuses_temperature(name: str) -> tuple[FunctionModel, list[Any]]:
    """A model that 400s the way OpenAI does whenever a temperature is sent,
    and the settings of every request it was sent."""
    sent: list[Any] = []

    def check(info: AgentInfo) -> None:
        sent.append(dict(info.model_settings or {}))
        if "temperature" in (info.model_settings or {}):
            raise ModelHTTPError(400, name, REJECTED)

    def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        check(info)
        return ModelResponse(parts=[TextPart("fine")])

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str]:
        check(info)
        yield "fine"

    return FunctionModel(answer, stream_function=stream, model_name=name), sent


@pytest.fixture(autouse=True)
def _forget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_factory, "_REJECTS_TEMPERATURE", set())


class TestATurnSurvivesIt:
    @pytest.mark.asyncio
    async def test_a_turn_retries_without_temperature(self) -> None:
        model, sent = _refuses_temperature("gpt-9-new")
        agent = Agent(tolerant(model), model_settings={"temperature": 0.7})

        result = await agent.run("hi")

        assert result.output == "fine"
        assert [("temperature" in s) for s in sent] == [True, False]

    @pytest.mark.asyncio
    async def test_a_streamed_turn_retries_too(self) -> None:
        model, sent = _refuses_temperature("gpt-9-new")
        agent = Agent(tolerant(model), model_settings={"temperature": 0.7})

        async with agent.run_stream("hi") as run:
            text = await run.get_output()

        assert text == "fine"
        assert [("temperature" in s) for s in sent] == [True, False]

    @pytest.mark.asyncio
    async def test_the_model_is_never_sent_one_again(self) -> None:
        model, _ = _refuses_temperature("gpt-9-new")
        await Agent(tolerant(model), model_settings={"temperature": 0.7}).run("hi")

        assert _supports_custom_temperature("gpt-9-new") is False
        assert _supports_custom_temperature("openai/gpt-9-new") is False
        assert _supports_custom_temperature("gpt-4o") is True

    @pytest.mark.asyncio
    async def test_any_other_rejection_still_fails(self) -> None:
        def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            raise ModelHTTPError(400, "gpt-9-new", {"message": "Bad max_tokens"})

        agent = Agent(
            tolerant(FunctionModel(answer, model_name="gpt-9-new")),
            model_settings={"temperature": 0.7},
        )

        with pytest.raises(ModelHTTPError):
            await agent.run("hi")
        assert _supports_custom_temperature("gpt-9-new") is True
