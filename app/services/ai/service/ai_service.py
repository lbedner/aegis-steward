"""``AIService``, composed from one mixin per concern, chained so each
layer may call the ones beneath it:

    AIServiceBase        state: settings, config, conversation manager
      └─ UsageMixin      token extraction, pricing, usage rollups
         └─ ContextsMixin   health/usage/catalog context builders
            └─ PromptMixin    persona overlay + per-request runtime construction
               └─ ChatMixin      conversation setup/teardown
                  └─ StreamingMixin  the turn: stream_chat, and chat draining it
                     └─ VoiceMixin     empty (voice off)
    StatusMixin          conversations, status, validation (base only)

The composition below is the whole class - it declares no methods of its
own. Callers import it from the facade: ``from app.services.ai.service
import AIService``.
"""

from app.services.ai.service.status import StatusMixin
from app.services.ai.service.voice import VoiceMixin


class AIService(VoiceMixin, StatusMixin):
    """
    Core AI service for chat functionality.

    Handles chat completions, conversation management, and provider abstraction.
    """
