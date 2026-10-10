"""Building a PydanticAI model for a provider."""

from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
import importlib
from typing import Any

from openai import AsyncOpenAI
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.providers import infer_provider_class
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import RunContext

from app.services.ai.config import AIServiceConfig
from app.services.ai.domains.llm.base import (
    ProviderError,
    require_api_key,
)
from app.services.ai.models import PROVIDERS, AIProvider


def _get_model_class(provider: AIProvider):
    """The pydantic-ai model class for a provider, imported lazily.

    Lazily because a provider nobody selected must not drag its SDK in;
    from the registry because the class is a FACT about the provider and
    belongs beside the rest of them.
    """
    spec = PROVIDERS.get(provider)
    if spec is None or spec.model is None:
        raise ProviderError(f"Unsupported provider: {provider}")
    module = importlib.import_module(spec.model[0])
    return getattr(module, spec.model[1])


# Models that refused a temperature this process, learned from the refusal
# (``tolerant``). The prefixes below are the starting guess; this is what a
# model newer than them teaches (gpt-6.1-sol, 2026-09-30).
# ponytail: per process - a restart relearns it at the cost of one refused
# request; persist it on the catalog row if that ever shows up in the logs.
_REJECTS_TEMPERATURE: set[str] = set()


def _bare_name(model: str) -> str:
    return model.split("/")[-1].split(":")[-1].lower()  # "provider/", "provider:"


def _supports_custom_temperature(model: str | None) -> bool:
    """Whether a model accepts a non-default ``temperature``.

    OpenAI reasoning models (o1/o3/o4) and the gpt-5 family reject any
    temperature other than the default (1) — sending one returns a 400. Omit
    temperature for those, and for any model that has refused one since
    this process started; every other model accepts a custom value.
    """
    if not model:
        return True
    name = _bare_name(model)
    return not (
        name.startswith(("o1", "o3", "o4", "gpt-5")) or name in _REJECTS_TEMPERATURE
    )


def _refused_temperature(error: ModelHTTPError, settings: ModelSettings) -> bool:
    """A 400 whose complaint is the temperature this request carried."""
    return (
        error.status_code == 400
        and "temperature" in settings
        and "temperature" in str(error.body)
    )


def _without_temperature(model_name: str, settings: ModelSettings) -> ModelSettings:
    """``settings`` minus the temperature ``model_name`` just refused, which
    it is never sent again."""
    _REJECTS_TEMPERATURE.add(_bare_name(model_name))
    kept = dict(settings)
    kept.pop("temperature", None)
    return ModelSettings(**kept)  # type: ignore[typeddict-item]


class _TemperatureTolerant(WrapperModel):
    """A model that, refused a temperature, retries the request once
    without it: a turn on a model the prefix list has not heard of still
    answers, instead of 400ing every time until somebody edits the list."""

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        try:
            return await super().request(
                messages, model_settings, model_request_parameters
            )
        except ModelHTTPError as error:
            if model_settings is None or not _refused_temperature(
                error, model_settings
            ):
                raise
            settings = _without_temperature(self.model_name, model_settings)
            return await super().request(messages, settings, model_request_parameters)

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        # The refusal comes as the request opens, before anything streams,
        # so only the opening is retried - never a stream already under way.
        async with AsyncExitStack() as stack:
            try:
                stream = await stack.enter_async_context(
                    super().request_stream(
                        messages, model_settings, model_request_parameters, run_context
                    )
                )
            except ModelHTTPError as error:
                if model_settings is None or not _refused_temperature(
                    error, model_settings
                ):
                    raise
                settings = _without_temperature(self.model_name, model_settings)
                stream = await stack.enter_async_context(
                    super().request_stream(
                        messages, settings, model_request_parameters, run_context
                    )
                )
            yield stream


def tolerant(model: Any) -> Any:
    """``model``, able to recover from refusing a temperature."""
    return _TemperatureTolerant(model)


def _model_settings(config: Any) -> ModelSettings:
    """Build ModelSettings for the agent, omitting ``temperature`` for models
    that only accept the default so they don't 400 (see
    ``_supports_custom_temperature``)."""
    kwargs: dict[str, Any] = {
        "max_tokens": config.max_tokens,
        "timeout": config.timeout_seconds,
    }
    if _supports_custom_temperature(config.model):
        kwargs["temperature"] = config.temperature
    # Effort is an Anthropic-only knob (thinking depth / token spend). Lazy
    # import mirrors ``_get_model_class``: the anthropic extra may not be
    # installed when another provider is configured.
    effort = getattr(config, "effort", None)
    if effort and getattr(config, "provider", None) == AIProvider.ANTHROPIC:
        from pydantic_ai.models.anthropic import AnthropicModelSettings

        return AnthropicModelSettings(anthropic_effort=effort, **kwargs)
    return ModelSettings(**kwargs)


def _ollama_model(config: AIServiceConfig, settings: Any) -> Any:
    """The model for a local Ollama server, over its OpenAI-compatible API.

    ``AsyncOpenAI``, ``OpenAIChatModel`` and ``OpenAIProvider`` are read off
    this module (not imported locally) so tests can patch them here.
    """
    openai_client = AsyncOpenAI(
        api_key="ollama",  # Ollama needs no auth; the client demands something
        base_url=f"{settings.ollama_base_url_effective}/v1",
    )
    # Ollama's OpenAI-compat endpoint silently DROPS the modern
    # ``max_completion_tokens`` field (only ``max_tokens`` maps to
    # num_predict), so route the cap to the legacy field or every
    # generation runs uncapped.
    from pydantic_ai.profiles.openai import OpenAIModelProfile

    return OpenAIChatModel(
        model_name=config.model,
        provider=OpenAIProvider(openai_client=openai_client),
        profile=OpenAIModelProfile(openai_chat_supports_max_completion_tokens=False),
    )


def _openai_compatible(
    model_name: str, base_url: str, api_key: str | None, profile: Any = None
) -> Any:
    """A model on any server that speaks the OpenAI API.

    One client, pointed elsewhere. The key goes to the CLIENT rather than
    into ``OPENAI_API_KEY``, so a stack holding both a real OpenAI key
    and an aggregator's keeps them apart - stamping the environment would
    let whichever was built last answer for both.
    """
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    client = AsyncOpenAI(api_key=api_key or "none", base_url=base_url)
    kwargs: dict[str, Any] = {"provider": OpenAIProvider(openai_client=client)}
    if profile is not None:
        kwargs["profile"] = profile
    return OpenAIChatModel(model_name, **kwargs)


def _grant_kwargs(
    tools: Sequence[Any] = (),
    capabilities: Sequence[Any] = (),
    agent_name: str | None = None,
) -> dict[str, Any]:
    """Agent kwargs for an agent row's grants, omitted when empty so
    pydantic-ai versions without capability support never see the kwarg.
    The name rides along so traces carry ``gen_ai.agent.name``."""
    kwargs: dict[str, Any] = {}
    if tools:
        kwargs["tools"] = list(tools)
    if capabilities:
        kwargs["capabilities"] = list(capabilities)
    if agent_name:
        kwargs["name"] = agent_name
    return kwargs


async def model_for(config: AIServiceConfig, settings: Any) -> tuple[Any, str]:
    """A bare model instance for ``config``, plus the model name it resolved to.

    ``get_agent`` wraps a whole ``Agent`` around this one. Callers that bring
    their own agent - chat_kit's ``ToolChatAgent``, headless jobs - need only
    the model, and have no way to reach one otherwise.

    Raises ``ProviderError`` for providers whose construction is not a plain
    model instance (the keyless public endpoints build their own clients); use
    ``get_agent`` for those.
    """
    if config.provider == AIProvider.OLLAMA:
        return tolerant(_ollama_model(config, settings)), config.model
    if config.provider in (AIProvider.PUBLIC, AIProvider.POLLINATIONS):
        raise ProviderError(
            f"model_for() does not support the {config.provider.value} provider; "
            "it builds its own client. Use get_agent() instead."
        )

    # The key goes to the client, never into the environment: a key
    # stamped there outlives the call and answers for every later model.
    key = await require_api_key(config, settings)

    # An OpenAI-compatible provider is a base URL and a key, and the URL
    # is the only thing distinguishing Mistral, Cohere and OpenRouter
    # from OpenAI itself - so it rides the registry and they share a path.
    #
    # That path builds a client. ``OpenAIChatModel`` takes no
    # ``base_url``: passing one raises TypeError, which is what these two
    # branches did the moment anybody selected them.
    spec = PROVIDERS.get(config.provider)
    if spec is not None and spec.base_url:
        return tolerant(
            _openai_compatible(config.model, spec.base_url, key)
        ), config.model
    # Our provider names are pydantic-ai's, so it names the provider class
    # (typed as the bare base; every concrete one takes the key).
    provider_class: Any = infer_provider_class(config.provider.value)
    provider = provider_class(api_key=key)
    model_class = _get_model_class(config.provider)
    return tolerant(model_class(config.model, provider=provider)), config.model


def validate_provider_support(provider: AIProvider) -> bool:
    """Check if a provider is supported in the current install.

    Catches ``ImportError`` (thrown lazily by pydantic-ai when an
    optional provider SDK isn't installed) in addition to our own
    ``ProviderError`` — a user who didn't select that provider via
    ``ai_providers`` at ``aegis init`` time has it effectively
    unsupported, not crashing.
    """
    try:
        _get_model_class(provider)
        return True
    except ProviderError, ImportError:
        return False


def get_provider_model_class(provider: AIProvider):
    """Get the PydanticAI model class for a provider."""
    return _get_model_class(provider)
