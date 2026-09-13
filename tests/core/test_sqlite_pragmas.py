"""Every SQLite connection opens with the pragmas the app relies on."""

from pathlib import Path
import sqlite3
from typing import Any

import pytest

from app.core.db import SQLITE_BUSY_TIMEOUT_MS, apply_sqlite_pragmas


def test_a_connection_gets_its_keys_and_its_wait(tmp_path: Path) -> None:
    """Both engines open the same way. Foreign keys because SQLite ships
    them off; the wait because SQLite has one writer at a time."""
    connection = sqlite3.connect(tmp_path / "app.db")
    try:
        apply_sqlite_pragmas(connection)
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert (
            connection.execute("PRAGMA busy_timeout").fetchone()[0]
            == SQLITE_BUSY_TIMEOUT_MS
        )
        # Deliberately NOT WAL: this file is shared across the container
        # boundary, where WAL's shared memory is not coherent.
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] != "wal"
    finally:
        connection.close()


class TestTwoWritersInOneTurn:
    """Live: a turn that proposed a change and then saved a memory died
    on "database is locked" - after the answer had already streamed and
    the fact had been said aloud.

    The 30-second ``busy_timeout`` was never reached. A bare BEGIN is
    DEFERRED: SQLite takes no lock until the first write and then has to
    UPGRADE, and an upgrade that finds another writer fails AT ONCE
    rather than waiting, because a transaction holding a read lock and
    waiting for a write lock is how two of them deadlock.

    BEGIN IMMEDIATE fixes that and costs too much: it makes every
    transaction a writer, reads included, and four containers sharing
    one rollback-journal file then serialise into a hang. So the engine
    keeps the deferred BEGIN and the upgrade is retried where it
    happens.
    """

    @pytest.mark.asyncio
    async def test_the_second_writer_queues_instead_of_failing(
        self, tmp_path: Path
    ) -> None:
        import asyncio

        from sqlalchemy import event, text
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.core.db import apply_sqlite_pragmas

        db = tmp_path / "probe.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db}")

        @event.listens_for(engine.sync_engine, "connect")
        def _connect(dbapi: Any, record: Any) -> None:
            dbapi.isolation_level = None
            apply_sqlite_pragmas(dbapi)

        async with engine.begin() as conn:
            await conn.execute(text("create table t (v int)"))

        async def read_then_write(value: int) -> None:
            async with engine.begin() as conn:
                await conn.execute(text("select count(*) from t"))
                await asyncio.sleep(0.2)  # hold it, the way a turn does
                await conn.execute(text("insert into t values (:v)"), {"v": value})

        from app.core.db import retry_on_locked

        await asyncio.gather(
            retry_on_locked(lambda: read_then_write(1)),
            retry_on_locked(lambda: read_then_write(2)),
        )

        async with engine.begin() as conn:
            landed = (await conn.execute(text("select count(*) from t"))).scalar()
        await engine.dispose()
        assert landed == 2, "both writers must land, not one and an error"

    def test_the_engine_does_not_make_every_read_a_writer(self) -> None:
        """IMMEDIATE here was a hang: four containers and a host CLI
        share this file, and with a rollback journal every read would
        queue behind every write."""
        import inspect

        from app.core.db import _async_sqlite_emit_begin

        assert "BEGIN IMMEDIATE" not in inspect.getsource(_async_sqlite_emit_begin)

    @pytest.mark.asyncio
    async def test_only_the_lock_is_retried(self) -> None:
        """Anything else is a problem to see, not to paper over."""
        from app.core.db import retry_on_locked

        async def boom() -> None:
            raise ValueError("not a lock")

        with pytest.raises(ValueError, match="not a lock"):
            await retry_on_locked(boom)

    @pytest.mark.asyncio
    async def test_a_lock_that_clears_is_not_an_error(self) -> None:
        from sqlalchemy.exc import OperationalError

        from app.core.db import retry_on_locked

        attempts = {"n": 0}

        async def flaky() -> str:
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise OperationalError("BEGIN", {}, Exception("database is locked"))
            return "written"

        assert await retry_on_locked(flaky) == "written"
        assert attempts["n"] == 2


class TestTheModeTheStackIntends:
    """``journal_mode`` lives in the FILE, survives every restart, and
    changing it needs exclusive access - so a connection that finds WAL
    cannot turn it off. That is how a ledger ran for weeks in the
    configuration its own module says would risk it: five containers and
    a host CLI sharing one bind-mounted file, and a night of "database
    is locked" before anybody read the pragma."""

    def test_a_fresh_connection_is_not_in_wal(self, tmp_path: Path) -> None:
        from app.core.db import warn_if_wal

        connection = sqlite3.connect(tmp_path / "app.db")
        try:
            apply_sqlite_pragmas(connection)
            assert warn_if_wal(connection) is None
        finally:
            connection.close()

    def test_the_check_leaves_no_cursor_open(self, tmp_path: Path) -> None:
        """A pragma read holds rows until they are fetched, and doing
        that inside the connect hook is how a session that has not run a
        query yet fails to commit - which it did."""
        connection = sqlite3.connect(tmp_path / "app.db")
        try:
            from app.core.db import warn_if_wal

            warn_if_wal(connection)
            connection.execute("create table t (v int)")
            connection.commit()
        finally:
            connection.close()

    def test_wal_is_reported_rather_than_tolerated(self, tmp_path: Path) -> None:
        """Silence is what let it drift."""
        from app.core.db import warn_if_wal

        connection = sqlite3.connect(tmp_path / "app.db")
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            assert warn_if_wal(connection) == "wal"
        finally:
            connection.close()

    def test_a_pragma_read_never_breaks_a_connect(self) -> None:
        """Whatever it cannot ask, it must not take the connection down
        with it - every session in the app opens through here."""
        from app.core.db import warn_if_wal

        class Refuses:
            def execute(self, _sql: str) -> Any:
                raise RuntimeError("no pragmas here")

        assert warn_if_wal(Refuses()) is None
