"""Shared state for the AIService facade mixins.

Every mixin in this package composes into ``AIService`` and assumes the
attributes declared here: the live settings object, the resolved config,
the conversation manager, and the lazily-constructed sub-services. The
service-level exception types live here too so every mixin (and external
caller) imports them from one place.
"""

from typing import Any

from app.services.ai.config import get_ai_config
from app.services.ai.domains.chat.conversation import ConversationManager


class AIServiceError(Exception):
    """Base exception for AI service errors."""

    pass


class ProviderError(AIServiceError):
    """Exception raised when AI provider fails."""

    pass


class ConversationError(AIServiceError):
    """Exception raised when conversation management fails."""

    pass


class AIServiceBase:
    """Holds the state every facade mixin shares."""

    def __init__(self, settings: Any):
        """Initialize AI service with configuration."""
        self.settings = settings
        self.config = get_ai_config(settings)
        self.conversation_manager = ConversationManager()

    def refresh_config(self) -> None:
        """Rebuild config from the live settings singleton.

        The service is constructed once at import, so its config would
        otherwise be frozen at whatever was active then. Callers that change
        the model - the runtime override, or a slash command writing .env -
        call this so the next request uses the new value.

        Reads the process-wide ``settings`` rather than a fresh ``Settings()``:
        re-reading .env here would discard a runtime override, which lives in
        the database and is applied onto this object.
        """
        from app.core.config import settings as live_settings

        self.settings = live_settings
        self.config = get_ai_config(live_settings)
