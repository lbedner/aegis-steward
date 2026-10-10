"""Shared state for the AIService facade mixins.

Every mixin in this package composes into ``AIService`` and assumes the
attributes declared here: the live settings object, the resolved config,
the conversation manager, and the lazily-constructed sub-services. The
service-level exception types live here too so every mixin (and external
caller) imports them from one place.
"""

from typing import Any

from app.services.ai.config import AIServiceConfig, get_ai_config
from app.services.ai.domains.chat.conversation import ConversationManager
from app.services.ai.domains.voice import STTService, TTSService


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
        self.conversation_manager = ConversationManager()
        self._stt_service: STTService | None = None
        self._tts_service: TTSService | None = None

    @property
    def config(self) -> AIServiceConfig:
        """The config from ``settings`` as they stand: the service is built
        once at import, and a value saved in the Overseer (applied as the
        process boots) or a model picked elsewhere applies on the next call."""
        return get_ai_config(self.settings)

    @property
    def stt(self) -> STTService:
        """Lazy initialization of STT service."""
        if self._stt_service is None:
            self._stt_service = STTService(self.settings)
        return self._stt_service

    @property
    def tts(self) -> TTSService:
        """Lazy initialization of TTS service."""
        if self._tts_service is None:
            self._tts_service = TTSService(self.settings)
        return self._tts_service

    def refresh_config(self) -> None:
        """Point the service at the live settings singleton, so ``config``
        reads the values a runtime override or a slash command applied.

        Reads the process-wide ``settings`` rather than a fresh ``Settings()``:
        re-reading .env here would discard a runtime override, which lives in
        the database and is applied onto this object.
        """
        from app.core.config import settings as live_settings

        self.settings = live_settings
