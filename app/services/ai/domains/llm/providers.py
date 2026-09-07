"""
AI provider factory for creating AI model instances.


Uses PydanticAI agents with support for multiple providers.

"""

from collections.abc import Sequence
import json
import os
from typing import Any

import httpx
from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from app.core.log import logger

# Re-exported so tests can patch ``app.services.ai.domains.llm.providers.<Name>``
# directly — patch targets follow the import path, not the source path,
# for clarity in test failure output.
__all__ = [
    "Agent",
    "ModelSettings",
    "AsyncOpenAI",
    "OpenAIChatModel",
    "OpenAIProvider",
]

from app.services.ai.config import AIServiceConfig
from app.services.ai.models import AIProvider


# Lazy loading of provider model classes to avoid import errors
def _get_model_class(provider: AIProvider):
    """Get model class for provider with lazy import to avoid dependency issues."""
    if provider == AIProvider.OPENAI:
        # The Responses API, not Chat Completions: OpenAI's reasoning
        # models (gpt-5.x, o-series) reject function tools on
        # /v1/chat/completions ("use /v1/responses"), and pydantic-ai's
        # Responses model speaks tools + streaming for the whole lineup.
        from pydantic_ai.models.openai import OpenAIResponsesModel

        return OpenAIResponsesModel
    elif provider == AIProvider.ANTHROPIC:
        from pydantic_ai.models.anthropic import AnthropicModel

        return AnthropicModel
    elif provider == AIProvider.GOOGLE:
        from pydantic_ai.models.google import GoogleModel

        return GoogleModel
    elif provider == AIProvider.GROQ:
        from pydantic_ai.models.groq import GroqModel

        return GroqModel
    elif provider == AIProvider.MISTRAL:
        from pydantic_ai.models.openai import OpenAIChatModel

        return OpenAIChatModel  # Mistral uses OpenAI-compatible API
    elif provider == AIProvider.COHERE:
        from pydantic_ai.models.openai import OpenAIChatModel

        return OpenAIChatModel  # Cohere can use OpenAI-compatible interface
    elif provider == AIProvider.OLLAMA:
        from pydantic_ai.models.openai import OpenAIChatModel

        return OpenAIChatModel  # Ollama uses OpenAI-compatible API
    elif provider == AIProvider.PUBLIC:
        from pydantic_ai.models.openai import OpenAIChatModel

        return OpenAIChatModel  # Public endpoints use OpenAI-compatible API
    elif provider == AIProvider.POLLINATIONS:
        from pydantic_ai.models.openai import OpenAIChatModel

        return OpenAIChatModel  # Pollinations uses OpenAI-compatible API
    else:
        raise ProviderError(f"Unsupported provider: {provider}")


class ProviderError(Exception):
    """Exception raised when provider setup fails."""

    pass


class ProviderNotInstalledError(ProviderError):
    """Raised when a provider's dependencies are not installed."""

    def __init__(self, provider: str, cli_command: str):
        self.provider = provider
        self.cli_command = cli_command
        super().__init__(f"{provider} provider is not installed")


def _supports_custom_temperature(model: str | None) -> bool:
    """Whether a model accepts a non-default ``temperature``.

    OpenAI reasoning models (o1/o3/o4) and the gpt-5 family reject any
    temperature other than the default (1) — sending one returns a 400. Omit
    temperature for those; every other model accepts a custom value.
    """
    if not model:
        return True
    name = model.split("/")[-1].lower()  # strip any "provider/" prefix
    return not name.startswith(("o1", "o3", "o4", "gpt-5"))


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


def model_for(config: AIServiceConfig, settings: Any) -> tuple[Any, str]:
    """A bare model instance for ``config``, plus the model name it resolved to.

    ``get_agent`` wraps a whole ``Agent`` around this one. Callers that bring
    their own agent - chat_kit's ``ToolChatAgent``, headless jobs - need only
    the model, and have no way to reach one otherwise.

    Raises ``ProviderError`` for providers whose construction is not a plain
    model instance (the keyless public endpoints build their own clients); use
    ``get_agent`` for those.
    """
    if config.provider == AIProvider.OLLAMA:
        return _ollama_model(config, settings), config.model
    if config.provider in (AIProvider.PUBLIC, AIProvider.POLLINATIONS):
        raise ProviderError(
            f"model_for() does not support the {config.provider.value} provider; "
            "it builds its own client. Use get_agent() instead."
        )

    provider_config = config.get_provider_config(settings)
    if not provider_config.api_key:
        raise ProviderError(
            f"No API key configured for {config.provider}. "
            f"Set {_get_env_var_name(config.provider)} environment variable. "
            f"For free usage without API keys, try the keyless 'public' or "
            f"'pollinations' providers, or Groq: https://console.groq.com/keys"
        )
    # PydanticAI 1.0+ reads credentials from the environment, not kwargs.
    _set_provider_env_var(config.provider, provider_config.api_key)

    model_kwargs: dict[str, Any] = {"model_name": config.model}
    if config.provider == AIProvider.MISTRAL:
        model_kwargs["base_url"] = "https://api.mistral.ai/v1"
    elif config.provider == AIProvider.COHERE:
        model_kwargs["base_url"] = "https://api.cohere.ai/v1"
    return _get_model_class(config.provider)(**model_kwargs), config.model


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


def get_agent(
    config: AIServiceConfig,
    settings: Any,
    system_prompt_override: str | None = None,
    *,
    tools: Sequence[Any] = (),
    capabilities: Sequence[Any] = (),
    agent_name: str | None = None,
) -> Agent:
    """
    Create a PydanticAI Agent for the configured provider.

    Falls back to demo mode if no API keys are configured for immediate testing.

    Args:
        config: AI service configuration
        settings: Application settings for API keys
        system_prompt_override: Optional custom system prompt (for RAG mode)
        tools: Registry-resolved tools granted to the requesting agent row
        capabilities: pydantic-ai capabilities (e.g. code mode) for the row
        agent_name: Agent name stamped on traces (``gen_ai.agent.name``)

    Returns:
        Agent: Configured PydanticAI Agent

    Raises:
        ProviderError: If agent creation fails
    """
    try:
        # Special handling for PUBLIC provider (anonymous tier works keyless;
        # LLM7_API_KEY unlocks premium models)
        if config.provider == AIProvider.PUBLIC:
            return _create_public_agent(
                config,
                system_prompt_override,
                tools=tools,
                capabilities=capabilities,
                agent_name=agent_name,
            )

        # Special handling for POLLINATIONS provider (anonymous tier works
        # keyless; POLLINATIONS_API_KEY selects an account tier)
        if config.provider == AIProvider.POLLINATIONS:
            return _create_pollinations_agent(
                config,
                system_prompt_override,
                tools=tools,
                capabilities=capabilities,
                agent_name=agent_name,
            )

        # Special handling for OLLAMA provider (local, no API key required)
        if config.provider == AIProvider.OLLAMA:
            return _create_ollama_agent(
                config,
                settings,
                system_prompt_override,
                tools=tools,
                capabilities=capabilities,
                agent_name=agent_name,
            )

        # Key check, env-var handoff, and model construction all live in
        # ``model_for`` so agent-building and model-only callers cannot drift.
        model, _ = model_for(config, settings)

        # Determine system prompt
        system_prompt = system_prompt_override or (
            "You are a helpful AI assistant. Provide clear, accurate, "
            "and concise responses. Be friendly and professional."
        )

        # Create agent with system prompt, model settings, and grants
        agent = Agent(
            model=model,
            model_settings=_model_settings(config),
            system_prompt=system_prompt,
            **_grant_kwargs(tools, capabilities, agent_name),
        )

        return agent

    except Exception as e:
        error_str = str(e)
        # Check for missing dependency error (pydantic-ai pattern)
        if "Please install" in error_str and "to use the" in error_str:
            # Raise specific exception - CLI will handle display (no logging here)
            provider_name = config.provider.value.lower()
            raise ProviderNotInstalledError(
                provider=config.provider.value,
                cli_command=f"aegis-steward ai add-provider {provider_name}",
            ) from e

        # Other errors still log and raise normally
        error_msg = f"Failed to create agent for {config.provider}: {e}"
        logger.error(error_msg)
        raise ProviderError(error_msg) from e


def _create_public_agent(
    config: AIServiceConfig,
    system_prompt_override: str | None = None,
    *,
    tools: Sequence[Any] = (),
    capabilities: Sequence[Any] = (),
    agent_name: str | None = None,
) -> Agent:
    """
    Create agent for PUBLIC provider using free public endpoints.

    Uses LLM7.io service which provides free access through an OpenAI-compatible API.

    Args:
        config: AI service configuration
        system_prompt_override: Optional custom system prompt (for RAG mode)
    """
    # Lazy imports - only needed for PUBLIC provider
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    try:
        # Create a custom HTTP client that fixes LLM7.io response format
        class FixedLLM7Client(httpx.AsyncClient):
            """Custom HTTP client that adds missing index field to LLM7.io responses."""

            async def send(self, request, **kwargs):
                response = await super().send(request, **kwargs)

                # If this is a chat completions request, fix the response
                if (
                    "/chat/completions" in str(request.url)
                    and request.method.upper() == "POST"
                ):
                    try:
                        # Check if this is a streaming response first
                        content_type = response.headers.get("content-type", "")
                        if (
                            "text/plain" in content_type
                            or "text/event-stream" in content_type
                            or "application/x-ndjson" in content_type
                        ):
                            return response

                        # Only process non-streaming JSON responses
                        if not response.headers.get("content-type", "").startswith(
                            "application/json"
                        ):
                            return response

                        # Get response text
                        response_text = response.text
                        data = json.loads(response_text)

                        # Add missing index field to choices
                        if "choices" in data and isinstance(data["choices"], list):
                            for i, choice in enumerate(data["choices"]):
                                if "index" not in choice or choice["index"] is None:
                                    choice["index"] = i

                        # Monkey patch the response to return fixed content
                        fixed_content = json.dumps(data)
                        response._content = fixed_content.encode()
                        response._text = fixed_content

                    except Exception as e:
                        logger.warning(f"Failed to fix LLM7.io response: {e}")

                return response

        # Create custom HTTP client
        custom_http_client = FixedLLM7Client()

        from .public_provider import LLM7_BASE_URL, public_api_key

        # Create the AsyncOpenAI client directly. LLM7.io requires a free
        # account key since mid-2026; public_api_key() warns when unset.
        openai_client = AsyncOpenAI(
            api_key=public_api_key(),
            base_url=LLM7_BASE_URL,
            http_client=custom_http_client,
        )

        # Create provider using the custom openai_client
        provider = OpenAIProvider(openai_client=openai_client)

        # Create OpenAI model using the provider. "auto" resolves against
        # LLM7's live catalog (their model list rotates).
        from .public_provider import resolve_public_model

        model_name = resolve_public_model(config.model)
        model = OpenAIChatModel(model_name=model_name, provider=provider)

        # Determine system prompt
        system_prompt = system_prompt_override or (
            "You are a helpful AI assistant powered by free public endpoints. "
            "Provide clear, accurate, and concise responses. "
            "Be friendly and professional."
        )

        # Create agent with system prompt, model settings, and grants
        agent = Agent(
            model=model,
            model_settings=_model_settings(config),
            system_prompt=system_prompt,
            **_grant_kwargs(tools, capabilities, agent_name),
        )

        return agent

    except Exception as e:
        error_msg = (
            f"Public endpoint failed ({e}). Here are reliable free alternatives:\n\n"
            "RECOMMENDED - Groq (Fastest, Most Generous Free Tier):\n"
            "   1. Visit: https://console.groq.com/keys\n"
            "   2. Get API key: export GROQ_API_KEY=your_key_here\n"
            "   3. Switch: aegis-steward ai config set-provider groq\n\n"
            "Google AI Studio (Also Free):\n"
            "   1. Visit: https://aistudio.google.com/app/apikey\n"
            "   2. Get API key: export GOOGLE_API_KEY=your_key_here\n"
            "   3. Switch: aegis-steward ai config set-provider google"
        )
        logger.error(f"Failed to create PUBLIC agent: {e}")
        raise ProviderError(error_msg) from e


def _create_pollinations_agent(
    config: AIServiceConfig,
    system_prompt_override: str | None = None,
    *,
    tools: Sequence[Any] = (),
    capabilities: Sequence[Any] = (),
    agent_name: str | None = None,
) -> Agent:
    """
    Create agent for the POLLINATIONS provider (keyless anonymous tier).

    Pollinations serves an OpenAI-compatible API; the anonymous tier
    needs no key but rejects ANY Authorization header, so keyless
    requests go through a client that strips it.

    Args:
        config: AI service configuration
        system_prompt_override: Optional custom system prompt (for RAG mode)
    """
    # Lazy imports - only needed for POLLINATIONS provider
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    from .pollinations_provider import (
        POLLINATIONS_BASE_URL,
        anonymous_async_http_client,
        pollinations_api_key,
        resolve_pollinations_model,
    )

    try:
        api_key = pollinations_api_key()
        if api_key:
            openai_client = AsyncOpenAI(api_key=api_key, base_url=POLLINATIONS_BASE_URL)
        else:
            # The SDK insists on a key; the anonymous client strips the
            # resulting header before it reaches the wire.
            openai_client = AsyncOpenAI(
                api_key="unused",
                base_url=POLLINATIONS_BASE_URL,
                http_client=anonymous_async_http_client(),
            )

        provider = OpenAIProvider(openai_client=openai_client)
        model_name = resolve_pollinations_model(config.model)
        model = OpenAIChatModel(model_name=model_name, provider=provider)

        system_prompt = system_prompt_override or (
            "You are a helpful AI assistant powered by free public endpoints. "
            "Provide clear, accurate, and concise responses. "
            "Be friendly and professional."
        )

        return Agent(
            model=model,
            model_settings=_model_settings(config),
            system_prompt=system_prompt,
            **_grant_kwargs(tools, capabilities, agent_name),
        )

    except Exception as e:
        error_msg = (
            f"Pollinations endpoint failed ({e}). The keyless 'public' provider "
            "(LLM7.io) is a drop-in alternative: "
            "aegis-steward ai config set-provider public"
        )
        logger.error(f"Failed to create POLLINATIONS agent: {e}")
        raise ProviderError(error_msg) from e


def _create_ollama_agent(
    config: AIServiceConfig,
    settings: Any,
    system_prompt_override: str | None = None,
    *,
    tools: Sequence[Any] = (),
    capabilities: Sequence[Any] = (),
    agent_name: str | None = None,
) -> Agent:
    """
    Create agent for OLLAMA provider using local Ollama server.

    Uses Ollama's OpenAI-compatible API endpoint.

    Args:
        config: AI service configuration
        settings: Application settings for base URL
        system_prompt_override: Optional custom system prompt (for RAG mode)
    """
    try:
        model = _ollama_model(config, settings)

        # Determine system prompt
        system_prompt = system_prompt_override or (
            "You are a helpful AI assistant running locally via Ollama. "
            "Provide clear, accurate, and concise responses. "
            "Be friendly and professional."
        )

        # Create agent with system prompt, model settings, and grants
        agent = Agent(
            model=model,
            model_settings=_model_settings(config),
            system_prompt=system_prompt,
            **_grant_kwargs(tools, capabilities, agent_name),
        )

        return agent

    except Exception as e:
        error_msg = (
            f"Ollama connection failed ({e}).\n\n"
            "Make sure Ollama is running:\n"
            "   1. Install Ollama: https://ollama.com/download\n"
            "   2. Start Ollama: ollama serve\n"
            "   3. Pull a model: ollama pull llama3.2\n"
            "   4. Sync models: aegis-steward llm sync --source=ollama\n\n"
            "If Ollama is running on a different host/port, set OLLAMA_BASE_URL in .env"
        )
        logger.error(f"Failed to create OLLAMA agent: {e}")
        raise ProviderError(error_msg) from e


def _get_env_var_name(provider: AIProvider) -> str:
    """Get the environment variable name for a provider."""
    env_var_map = {
        AIProvider.OPENAI: "OPENAI_API_KEY",
        AIProvider.ANTHROPIC: "ANTHROPIC_API_KEY",
        AIProvider.GOOGLE: "GOOGLE_API_KEY",
        AIProvider.GROQ: "GROQ_API_KEY",
        AIProvider.MISTRAL: "MISTRAL_API_KEY",
        AIProvider.COHERE: "COHERE_API_KEY",
        AIProvider.OLLAMA: "OLLAMA_API_KEY",
        AIProvider.PUBLIC: "PUBLIC_API_KEY",
        AIProvider.POLLINATIONS: "POLLINATIONS_API_KEY",
    }

    result = env_var_map.get(provider)
    if result:
        return result
    else:
        return f"{str(provider).upper()}_API_KEY"


def _set_provider_env_var(provider: AIProvider, api_key: str) -> None:
    """Set environment variable for provider API key."""
    env_var_map = {
        AIProvider.OPENAI: "OPENAI_API_KEY",
        AIProvider.ANTHROPIC: "ANTHROPIC_API_KEY",
        AIProvider.GOOGLE: "GOOGLE_API_KEY",
        AIProvider.GROQ: "GROQ_API_KEY",
        AIProvider.MISTRAL: "MISTRAL_API_KEY",
        AIProvider.COHERE: "COHERE_API_KEY",
        AIProvider.OLLAMA: "OLLAMA_API_KEY",
        AIProvider.PUBLIC: "PUBLIC_API_KEY",
        AIProvider.POLLINATIONS: "POLLINATIONS_API_KEY",
    }

    env_var = env_var_map.get(provider)
    if env_var and api_key:
        os.environ[env_var] = api_key


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
    except (ProviderError, ImportError):
        return False


def get_supported_providers() -> list[AIProvider]:
    """Get list of supported providers."""
    return [
        AIProvider.OPENAI,
        AIProvider.ANTHROPIC,
        AIProvider.GOOGLE,
        AIProvider.GROQ,
        AIProvider.MISTRAL,
        AIProvider.COHERE,
        AIProvider.OLLAMA,
        AIProvider.PUBLIC,
        AIProvider.POLLINATIONS,
    ]


def get_provider_model_class(provider: AIProvider):
    """Get the PydanticAI model class for a provider."""
    return _get_model_class(provider)
