"""Voice pipeline placeholder: ships only when the voice option is enabled."""

from app.services.ai.service.streaming import StreamingMixin


class VoiceMixin(StreamingMixin):
    """Empty link in the mixin chain; the voice option is off."""
