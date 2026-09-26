"""Speech settings for ai[voice], kept out of config.py's size budget.

``Settings`` inherits these, so they read as ``settings.STT_MODEL`` and load
from ``.env`` like every other setting.
"""

from typing import Literal

from pydantic import Field
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
    # Sent to the model as ``speed`` (it used to be read and never sent).
    TTS_SPEED: float = Field(default=1.0, ge=0.25, le=4.0)
    # How she says it - tone, emotion, pacing - sent as ``instructions``
    # (gpt-4o models only). OpenAI's documented way to steer delivery.
    TTS_INSTRUCTIONS: str | None = None

    # How a spoken turn's reply is heard (#261). "live": each sentence she
    # streams, narration included, as soon as it is complete. "answer": the
    # settled answer, once. A list, not a flag, so a later mode (Realtime,
    # #252) is one more value rather than new plumbing.
    VOICE_REPLY: Literal["answer", "live"] = "live"
    # What plays while she works and has said nothing yet: soft generated
    # key taps ("typing"), or nothing. Instead of narrating her tool calls,
    # which ran behind her own answer (2026-09-25).
    VOICE_WORKING_SOUND: Literal["typing", "none"] = "typing"
    # A live call hangs up after this many seconds of dead air (0: never):
    # GPT-Live bills by the minute, silence included.
    VOICE_LIVE_IDLE_SECONDS: int = Field(default=30, ge=0, le=600)
