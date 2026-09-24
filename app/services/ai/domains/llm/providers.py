"""Stable import surface for provider models and agents.

Provider validation, model construction, and agent construction live in their
own modules. Callers can continue importing them from ``providers``.
"""

from app.services.ai.domains.llm.agents import get_agent as get_agent
from app.services.ai.domains.llm.base import (
    SUPPORTED_PROVIDERS as SUPPORTED_PROVIDERS,
)
from app.services.ai.domains.llm.base import (
    ProviderError as ProviderError,
)
from app.services.ai.domains.llm.base import (
    ProviderNotInstalledError as ProviderNotInstalledError,
)
from app.services.ai.domains.llm.base import (
    get_supported_providers as get_supported_providers,
)
from app.services.ai.domains.llm.base import (
    require_api_key as require_api_key,
)
from app.services.ai.domains.llm.model_factory import (
    AsyncOpenAI as AsyncOpenAI,
)
from app.services.ai.domains.llm.model_factory import (
    get_provider_model_class as get_provider_model_class,
)
from app.services.ai.domains.llm.model_factory import (
    model_for as model_for,
)
from app.services.ai.domains.llm.model_factory import (
    validate_provider_support as validate_provider_support,
)
from app.services.ai.models import PROVIDERS, AIProvider


def _get_env_var_name(provider: AIProvider) -> str:
    """The API-key setting recorded in the provider registry."""
    return PROVIDERS[provider].env_var
