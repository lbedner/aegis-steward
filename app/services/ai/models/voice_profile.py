"""How the assistant hears and speaks, as rows the app changes (#262).

Like the active model (``llm_active_selection``), a voice is table data:
switching or editing it takes effect on the next thing she says, with no
``.env`` edit and no restart. ``.env`` only seeds the first profile.
"""

from datetime import datetime

from sqlmodel import Field, SQLModel

from app.core.clock import utcnow


class VoiceProfile(SQLModel, table=True):
    """One named way of hearing and speaking; exactly one is active."""

    __tablename__ = "voice_profile"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    is_active: bool = Field(default=False, index=True)
    # Hearing.
    stt_provider: str = Field(default="openai_whisper")
    stt_model: str | None = None
    # Speaking. ``tts_instructions`` is OpenAI's labeled delivery text
    # (Voice Affect, Tone, Emotion, Pronunciation, Pauses, Pacing).
    tts_provider: str = Field(default="openai")
    tts_model: str | None = None
    tts_voice: str | None = None
    tts_speed: float = Field(default=1.0, ge=0.25, le=4.0)
    tts_instructions: str | None = None
    # How a spoken turn's reply is heard, and what plays while she works.
    reply: str = Field(default="live")
    working_sound: str = Field(default="typing")
    updated_at: datetime = Field(default_factory=utcnow)
