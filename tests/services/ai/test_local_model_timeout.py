"""A local model reads the whole prompt before it answers a byte.

Ollama sends nothing until prefill ends: a 27B model at ~200 tokens a
second spends half a minute on a 6k prompt and over two on a full 32k
context, so the hosted models' 120 seconds cut a turn mid-read. A retry
then sends the same prompt to a server still reading the abandoned one,
and waits behind it.
"""

from unittest.mock import MagicMock, patch

from app.services.ai.config import AIServiceConfig
from app.services.ai.domains.llm.model_factory import (
    LOCAL_TIMEOUT_SECONDS,
    _model_settings,
    _ollama_model,
)
from app.services.ai.models import AIProvider


def test_a_local_model_waits_out_its_prefill() -> None:
    local = AIServiceConfig(
        provider=AIProvider.OLLAMA, model="qwen", timeout_seconds=120
    )
    hosted = AIServiceConfig(
        provider=AIProvider.OPENAI, model="gpt", timeout_seconds=120
    )

    assert _model_settings(local)["timeout"] == LOCAL_TIMEOUT_SECONDS
    assert _model_settings(hosted)["timeout"] == 120


def test_a_local_model_is_never_asked_twice() -> None:
    settings = MagicMock(ollama_base_url_effective="http://localhost:11434")
    config = AIServiceConfig(provider=AIProvider.OLLAMA, model="qwen")

    with (
        patch("app.services.ai.domains.llm.model_factory.AsyncOpenAI") as client,
        patch("app.services.ai.domains.llm.model_factory.OpenAIChatModel"),
        patch("app.services.ai.domains.llm.model_factory.OpenAIProvider"),
    ):
        _ollama_model(config, settings)

    assert client.call_args.kwargs["max_retries"] == 0
