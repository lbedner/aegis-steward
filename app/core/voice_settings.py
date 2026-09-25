"""Speech settings for ai[voice], kept out of config.py's size budget.

``Settings`` inherits these, so they read as ``settings.STT_MODEL`` and load
from ``.env`` like every other setting.
"""

from pydantic_settings import BaseSettings


class VoiceSettings(BaseSettings):
    # Speech-to-Text (STT) Configuration
    # Provider: openai_whisper, whisper_local, faster_whisper, groq_whisper
    STT_PROVIDER: str = "openai_whisper"  # Default to OpenAI Whisper API
    STT_MODEL: str | None = None  # Model name/size (provider-specific, None = default)
    STT_LANGUAGE: str | None = None  # ISO 639-1 language code (None = auto-detect)
    # For local providers (whisper_local, faster_whisper)
    STT_DEVICE: str | None = None  # Device: 'cpu', 'cuda', 'mps' (None = auto-detect)

    # Text-to-Speech (TTS) Configuration
    # Provider: openai
    TTS_PROVIDER: str = "openai"  # Default to OpenAI TTS API
    TTS_MODEL: str | None = (
        None  # Model: tts-1 (fast), tts-1-hd (quality). None = default
    )
    TTS_VOICE: str | None = (
        None  # Voice: alloy, echo, fable, onyx, nova, shimmer. None = alloy
    )
    TTS_SPEED: float = 1.0  # Speech speed multiplier (0.25 to 4.0)
