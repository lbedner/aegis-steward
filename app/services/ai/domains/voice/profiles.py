"""Voice profiles: which one is active, and putting it to work (#262).

The active profile is applied onto the running settings, the way the active
model is, so everything that already reads ``settings.TTS_*`` / ``STT_*`` /
``VOICE_*`` follows it unchanged. The speech services read their config once
when built, so applying a profile also drops them for the next call to
rebuild.
"""

from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.clock import utcnow
from app.services.ai.domains.voice import queries
from app.services.ai.models.voice_profile import VoiceProfile

SEEDED_NAME = "Illiana"

# Profile column -> the setting it drives. One table, read both ways: to
# seed a profile from .env and to put a profile onto the settings.
SETTINGS = {
    "stt_provider": "STT_PROVIDER",
    "stt_model": "STT_MODEL",
    "tts_provider": "TTS_PROVIDER",
    "tts_model": "TTS_MODEL",
    "tts_voice": "TTS_VOICE",
    "tts_speed": "TTS_SPEED",
    "tts_instructions": "TTS_INSTRUCTIONS",
    "reply": "VOICE_REPLY",
    "working_sound": "VOICE_WORKING_SOUND",
}


# What a profile may say, from OpenAI's docs (2026-09): marin and cedar are
# the recommended voices; the dated gpt-4o-mini-tts builds can be pinned (the
# March one is more expressive, the December one mishears less); tts-1 takes
# no delivery instructions and knows only the first nine voices.
TTS_VOICES = (
    "marin", "cedar", "alloy", "ash", "ballad", "coral", "echo",
    "fable", "nova", "onyx", "sage", "shimmer", "verse",
)  # fmt: skip
TTS_MODELS = (
    "gpt-4o-mini-tts",
    "gpt-4o-mini-tts-2025-03-20",
    "gpt-4o-mini-tts-2025-12-15",
    "tts-1",
    "tts-1-hd",
)
STT_MODELS = ("gpt-transcribe", "gpt-4o-transcribe", "gpt-4o-mini-transcribe")
REPLIES = ("live", "answer")
WORKING_SOUNDS = ("typing", "none")
SPEED_RANGE = (0.25, 4.0)


def parse_form(form: Any) -> tuple[dict[str, Any], list[str]]:
    """A profile's edit form as column values, and what is wrong with it."""
    errors: list[str] = []
    name = str(form.get("name") or "").strip()
    if not name:
        errors.append("A voice needs a name.")
    choices = {
        "tts_voice": TTS_VOICES,
        "tts_model": TTS_MODELS,
        "stt_model": STT_MODELS,
        "reply": REPLIES,
        "working_sound": WORKING_SOUNDS,
    }
    values: dict[str, Any] = {"name": name}
    for column, allowed in choices.items():
        value = str(form.get(column) or "")
        if value not in allowed:
            errors.append(f"{column.replace('_', ' ')}: pick one of the choices.")
        values[column] = value
    try:
        speed = float(str(form.get("tts_speed") or ""))
        if not SPEED_RANGE[0] <= speed <= SPEED_RANGE[1]:
            raise ValueError
        values["tts_speed"] = speed
    except ValueError:
        errors.append(f"Speed is a number from {SPEED_RANGE[0]} to {SPEED_RANGE[1]}.")
    values["tts_instructions"] = str(form.get("tts_instructions") or "").strip() or None
    return values, errors


def settings_for(profile: VoiceProfile, settings: Any) -> Any:
    """A copy of ``settings`` speaking as ``profile`` - for a preview, which
    must not change the voice she is using."""
    return settings.model_copy(
        update={key: getattr(profile, column) for column, key in SETTINGS.items()}
    )


class VoiceProfileError(ValueError):
    """A profile change that cannot be made (a duplicate name, a missing row)."""


list_profiles = queries.all_profiles
active_profile = queries.active_profile


async def seed_from_settings(
    session: AsyncSession, settings: Any
) -> VoiceProfile | None:
    """The first profile, from .env, made active - only when there is none.
    Returns it, or None when profiles already exist (they are the app's)."""
    if await queries.any_profile(session):
        return None
    seeded = VoiceProfile(
        name=SEEDED_NAME,
        is_active=True,
        **{column: getattr(settings, key) for column, key in SETTINGS.items()},
    )
    session.add(seeded)
    await session.commit()
    return seeded


async def create(session: AsyncSession, *, name: str, **changes: Any) -> VoiceProfile:
    """A new, inactive profile: the active one's values, with ``changes``."""
    name = name.strip()
    if not name:
        raise VoiceProfileError("A voice needs a name.")
    if await queries.profile_named(session, name) is not None:
        raise VoiceProfileError(f'There is already a voice called "{name}".')
    base = await active_profile(session)
    values = {column: getattr(base, column) for column in SETTINGS} if base else {}
    values.update(changes)
    made = VoiceProfile(name=name, is_active=False, **values)
    session.add(made)
    await session.commit()
    return made


async def update(
    session: AsyncSession, profile_id: int, **changes: Any
) -> VoiceProfile:
    profile = await session.get(VoiceProfile, profile_id)
    if profile is None:
        raise VoiceProfileError("That voice no longer exists.")
    name = changes.get("name")
    if name and name != profile.name and await queries.profile_named(session, name):
        raise VoiceProfileError(f'There is already a voice called "{name}".')
    for column, value in changes.items():
        setattr(profile, column, value)
    profile.updated_at = utcnow()
    session.add(profile)
    await session.commit()
    return profile


async def activate(session: AsyncSession, profile_id: int) -> list[VoiceProfile]:
    """Make ``profile_id`` the one active profile. Returns every profile, so
    a caller showing the list does not read it again. (Sessions keep objects
    loaded after a commit: no refresh round trips here.)"""
    profiles = await list_profiles(session)
    chosen = next((p for p in profiles if p.id == profile_id), None)
    if chosen is None:
        raise VoiceProfileError("That voice no longer exists.")
    for profile in profiles:
        if profile.is_active != (profile is chosen):
            profile.is_active = profile is chosen
            session.add(profile)
    await session.commit()
    return profiles


def apply(profile: VoiceProfile, settings: Any, service: Any) -> None:
    """Put ``profile`` onto the running ``settings``, and drop the speech
    services ``service`` built from the old values."""
    for column, key in SETTINGS.items():
        setattr(settings, key, getattr(profile, column))
    service._stt_service = None
    service._tts_service = None


async def load_active(
    session: AsyncSession, settings: Any, service: Any
) -> VoiceProfile | None:
    """At startup: seed a first profile from .env if there is none, then run
    on the active one. After the first boot the table wins over .env."""
    await seed_from_settings(session, settings)
    active = await active_profile(session)
    if active is not None:
        apply(active, settings, service)
    return active
