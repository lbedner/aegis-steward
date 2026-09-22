"""The test engines are shaped like the real one.

``app.core.db`` emits ``BEGIN IMMEDIATE`` so that ``busy_timeout``
applies - a deferred BEGIN fails its lock upgrade at once instead of
waiting. The suite's engines emitted a plain ``BEGIN``, set no
``busy_timeout`` and no WAL, so an open session in a test was not the
write lock and NO TEST COULD REPRODUCE ANY LOCK-CONTENTION BUG. Two
shipped in a week: an import that held the lock for its whole file, and
an approve that waited thirty seconds on itself.

The shape is derived from ``app.core.db``'s own functions rather than
restated here, so a change to the real pragmas cannot miss the engines
the suite runs on.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel.ext.asyncio.session import AsyncSession


async def _one(engine: AsyncEngine, sql: str) -> Any:
    async with engine.connect() as conn:
        return (await conn.exec_driver_sql(sql)).scalar()


@pytest.mark.asyncio
async def test_the_app_owned_engine_is_in_wal_with_the_real_timeout(
    app_owned_engine: AsyncEngine,
) -> None:
    from app.core.db import SQLITE_BUSY_TIMEOUT_MS

    assert await _one(app_owned_engine, "PRAGMA journal_mode") == "wal"
    assert await _one(app_owned_engine, "PRAGMA busy_timeout") == SQLITE_BUSY_TIMEOUT_MS
    assert await _one(app_owned_engine, "PRAGMA foreign_keys") == 1


@pytest.mark.asyncio
async def test_an_open_session_is_the_write_lock(
    impatient_engine: AsyncEngine,
) -> None:
    """The whole point of the ticket. A session open against a
    production-shaped engine HOLDS the write lock, so a second writer
    waits and then gives up - which is what makes "held across a model
    call" a thing a test can catch at all. Against the old engines this
    passed straight through."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.ext.asyncio import async_sessionmaker

    maker = async_sessionmaker(impatient_engine, class_=AsyncSession)
    async with maker() as holder:
        await holder.execute(text("SELECT 1"))

        async with maker() as other:
            with pytest.raises(OperationalError, match="database is locked"):
                await other.execute(text("SELECT 1"))


@pytest.mark.asyncio
async def test_the_impatient_engine_waits_only_a_moment(
    impatient_engine: AsyncEngine,
) -> None:
    """Same shape, a timeout short enough to assert against: a test that
    proves the lock is held must not take thirty seconds to say so."""
    from app.core.db import SQLITE_BUSY_TIMEOUT_MS
    from tests._sqlite import IMPATIENT_BUSY_TIMEOUT_MS

    assert IMPATIENT_BUSY_TIMEOUT_MS < SQLITE_BUSY_TIMEOUT_MS
    assert (
        await _one(impatient_engine, "PRAGMA busy_timeout") == IMPATIENT_BUSY_TIMEOUT_MS
    )
    assert await _one(impatient_engine, "PRAGMA journal_mode") == "wal"
