"""Comms' reads: the devices web push reaches."""

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from .models import PushSubscription


async def push_devices(session: AsyncSession) -> list[PushSubscription]:
    return list((await session.exec(select(PushSubscription))).all())


async def push_device(session: AsyncSession, endpoint: str) -> PushSubscription | None:
    return (
        await session.exec(
            select(PushSubscription).where(PushSubscription.endpoint == endpoint)
        )
    ).first()
