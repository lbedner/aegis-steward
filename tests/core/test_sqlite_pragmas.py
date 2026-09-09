"""Both SQLite engines wait for the write lock.

One writer at a time is SQLite's rule; a connection without a busy
timeout fails the instant another holds the lock. The async engine had
the timeout; the sync engine (the conversation store, the usage
recorder) did not, and a chat turn's final save failed with "database
is locked" after its answer had streamed.
"""

from sqlalchemy import text

from app.core.db import SQLITE_BUSY_TIMEOUT_MS, async_engine, engine


def test_sync_connections_wait_for_the_lock() -> None:
    with engine.connect() as conn:
        assert (
            conn.execute(text("PRAGMA busy_timeout")).scalar() == SQLITE_BUSY_TIMEOUT_MS
        )
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


async def test_async_connections_wait_for_the_lock() -> None:
    async with async_engine.connect() as conn:
        assert (
            await conn.execute(text("PRAGMA busy_timeout"))
        ).scalar() == SQLITE_BUSY_TIMEOUT_MS
