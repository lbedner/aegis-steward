"""A secret read never waits on the write lock.

Every transaction begins IMMEDIATE (``app.core.db``): an open session is
the app's one write lock from its first statement, a SELECT included. The
secrets store opens a session of its own, so a read on a cold cache from
code that had queried its own session and not yet committed waited the
whole busy timeout behind itself. That was the Settings > Comms hang
(2026-10-15: the bill preview's query, then the push key). A read takes
no write lock at all (``read_only``), so it cannot wait, whoever asks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.components.secrets.store import DatabaseStore
from app.core.cache import get_cache
import app.core.db as db


@pytest.fixture
async def holder(
    impatient_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncSession]:
    """A session that has queried and not committed: it holds the lock,
    and the store's own sessions come from the same database."""
    maker = async_sessionmaker(
        impatient_engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(db, "AsyncSessionLocal", maker)
    await get_cache().clear()
    async with maker() as session:
        await session.exec(text("select 1"))  # type: ignore[call-overload]
        yield session
        await session.rollback()


async def test_a_read_inside_an_open_session_does_not_wait(
    holder: AsyncSession,
) -> None:
    assert await DatabaseStore().get_many(["RESEND_API_KEY"]) == {
        "RESEND_API_KEY": None
    }


async def test_the_overseer_listing_does_not_wait(holder: AsyncSession) -> None:
    assert await DatabaseStore().stored() == {}
