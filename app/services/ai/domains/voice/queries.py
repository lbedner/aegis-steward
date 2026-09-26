"""Reads for the voice domain: which profiles exist, and which is active."""

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.ai.models.voice_profile import VoiceProfile


async def all_profiles(session: AsyncSession) -> list[VoiceProfile]:
    """Every voice profile, by name."""
    return list(
        (
            await session.exec(select(VoiceProfile).order_by(col(VoiceProfile.name)))
        ).all()
    )


async def active_profile(session: AsyncSession) -> VoiceProfile | None:
    return (
        await session.exec(select(VoiceProfile).where(col(VoiceProfile.is_active)))
    ).first()


async def profile_named(session: AsyncSession, name: str) -> VoiceProfile | None:
    return (
        await session.exec(select(VoiceProfile).where(VoiceProfile.name == name))
    ).first()


async def any_profile(session: AsyncSession) -> bool:
    return (await session.exec(select(VoiceProfile.id))).first() is not None
