"""Every SQLite connection opens with the pragmas the app relies on.

The stack ran for months in the OTHER configuration, and the comments
here are the reason it had to: the database file was bind-mounted from
the host into four containers, so the host CLI and the containers were
two kernels looking at one file. WAL coordinates its readers through
shared memory mapped from a ``-shm``, which is only coherent on one
kernel, so WAL was unsafe - and without WAL, taking the write lock up
front would have made every read a writer and serialised the app into a
hang. What was left was a deferred BEGIN whose lock UPGRADE fails at
once, by design, and twelve hand-placed retries around it.

The file now lives on a named volume that the container boundary never
crosses. One kernel, so WAL is safe, so the lock is taken up front, so
``busy_timeout`` finally applies to writes and no call site has to know
SQLite has one writer.
"""

import asyncio
from pathlib import Path
import sqlite3
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.db import SQLITE_BUSY_TIMEOUT_MS, apply_sqlite_pragmas, ensure_wal


def _engine(db: Path) -> Any:
    """An async engine wired the way the app wires its own: the mode set
    once on the file, the pragmas on every connection."""
    plain = sqlite3.connect(db)
    try:
        ensure_wal(plain)
    finally:
        plain.close()

    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")

    @event.listens_for(engine.sync_engine, "connect")
    def _connect(dbapi: Any, record: Any) -> None:
        dbapi.isolation_level = None
        apply_sqlite_pragmas(dbapi)

    @event.listens_for(engine.sync_engine, "begin")
    def _begin(conn: Any) -> None:
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


def test_a_connection_gets_its_keys_and_its_wait(tmp_path: Path) -> None:
    """Foreign keys because SQLite ships them off. The wait because
    SQLite has one writer at a time."""
    connection = sqlite3.connect(tmp_path / "app.db")
    try:
        apply_sqlite_pragmas(connection)
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert (
            connection.execute("PRAGMA busy_timeout").fetchone()[0]
            == SQLITE_BUSY_TIMEOUT_MS
        )
    finally:
        connection.close()


def test_the_connect_hook_asks_no_pragma_that_answers(tmp_path: Path) -> None:
    """A row left unfetched inside the connect hook is how a session
    that has not run a query yet fails to commit. Closing the cursor is
    not a fix either: under aiosqlite ``close()`` is a coroutine, so a
    synchronous call returns an un-awaited object and closes nothing.
    That is why the mode is set at startup instead, and why this test
    reads the SOURCE rather than the behaviour - the failure only shows
    up on the async driver."""
    import inspect

    source = inspect.getsource(apply_sqlite_pragmas)
    body = source.split('"""')[-1]
    assert "journal_mode" not in body, "journal_mode returns a row; set it at startup"


class TestTwoWritersInOneTurn:
    """Live: a turn that proposed a change and then saved a memory died
    on "database is locked" - after the answer had already streamed and
    the fact had been said aloud.

    The 30-second ``busy_timeout`` was never reached. A bare BEGIN is
    DEFERRED: SQLite takes no lock until the first write and then has to
    UPGRADE, and an upgrade that finds another writer fails AT ONCE
    rather than waiting, because a transaction holding a read lock while
    waiting for a write lock is how two of them deadlock.

    BEGIN IMMEDIATE takes the lock up front, where there is no upgrade
    to fail and waiting is safe. It was unaffordable while the file was
    bind-mounted; on one kernel with WAL it is simply correct.
    """

    @pytest.mark.asyncio
    async def test_the_second_writer_waits_and_lands(self, tmp_path: Path) -> None:
        """No retry helper anywhere in sight: the engine handles it."""
        engine = _engine(tmp_path / "probe.db")
        async with engine.begin() as conn:
            await conn.execute(text("create table t (v int)"))

        async def read_then_write(value: int) -> None:
            async with engine.begin() as conn:
                await conn.execute(text("select count(*) from t"))
                await asyncio.sleep(0.2)  # hold it, the way a turn does
                await conn.execute(text("insert into t values (:v)"), {"v": value})

        await asyncio.gather(read_then_write(1), read_then_write(2))

        async with engine.begin() as conn:
            landed = (await conn.execute(text("select count(*) from t"))).scalar()
        await engine.dispose()
        assert landed == 2, "both writers must land, not one and an error"

    @pytest.mark.asyncio
    async def test_a_reader_is_not_blocked_by_an_open_writer(
        self, tmp_path: Path
    ) -> None:
        """The other half of the bargain. Taking the write lock up front
        is only affordable because under WAL a reader never queues
        behind it - which is exactly what made IMMEDIATE a hang before."""
        db = tmp_path / "readers.db"
        engine = _engine(db)
        async with engine.begin() as conn:
            await conn.execute(text("create table t (v int)"))
            await conn.execute(text("insert into t values (1)"))

        writing = asyncio.Event()
        release = asyncio.Event()

        async def hold_a_write() -> None:
            async with engine.begin() as conn:
                await conn.execute(text("insert into t values (2)"))
                writing.set()
                await release.wait()

        writer = asyncio.create_task(hold_a_write())
        await asyncio.wait_for(writing.wait(), timeout=5)

        # A separate connection, reading while that write is open.
        reader = create_async_engine(f"sqlite+aiosqlite:///{db}")
        try:
            async with reader.connect() as conn:
                seen = (await conn.execute(text("select count(*) from t"))).scalar()
        finally:
            await reader.dispose()

        release.set()
        await writer
        await engine.dispose()
        # One row: the writer's second row is not committed yet, and the
        # reader saw a consistent snapshot rather than waiting for it.
        assert seen == 1


class TestTheLockIsTakenUpFront:
    def test_the_engine_does_not_emit_a_deferred_begin(self) -> None:
        import inspect

        from app.core.db import _async_sqlite_emit_begin

        source = inspect.getsource(_async_sqlite_emit_begin)
        assert "BEGIN IMMEDIATE" in source

    def test_no_module_emits_a_bare_begin(self) -> None:
        """A bare BEGIN anywhere reintroduces the upgrade that cannot
        wait, in one place, silently."""
        import re

        offenders = []
        for path in Path("app").rglob("*.py"):
            for number, line in enumerate(
                path.read_text().splitlines(), start=1
            ):
                if re.search(r"""exec_driver_sql\(\s*["']BEGIN["']""", line):
                    offenders.append(f"{path}:{number}")
        assert not offenders, f"bare BEGIN: {offenders}"


class TestTheModeTheStackIntends:
    """``journal_mode`` lives in the FILE and survives every restart, so
    it is asked for once at startup rather than on every connection.

    The guard used to point the other way, and it was right to: across a
    bind mount WAL would have risked the ledger, and all the code could
    do was warn. On a named volume it can simply ask for the mode it
    needs, so setting and checking are one call."""

    def test_it_puts_a_fresh_file_into_wal(self, tmp_path: Path) -> None:
        connection = sqlite3.connect(tmp_path / "app.db")
        try:
            assert ensure_wal(connection) is None
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            connection.close()

    def test_it_leaves_no_cursor_open(self, tmp_path: Path) -> None:
        """A pragma that answers holds rows until they are fetched, and
        a statement still in progress is a commit that fails."""
        connection = sqlite3.connect(tmp_path / "app.db")
        try:
            ensure_wal(connection)
            connection.execute("create table t (v int)")
            connection.commit()
        finally:
            connection.close()

    def test_a_mode_it_cannot_set_is_reported(self, tmp_path: Path) -> None:
        """A file that will not go into WAL is the old outage waiting to
        happen: the engine takes the write lock up front, so every read
        would queue behind every write. Silence is what let the old
        configuration drift for weeks."""
        from unittest.mock import Mock

        connection = Mock()
        connection.execute.return_value.fetchone.return_value = ("delete",)
        assert ensure_wal(connection) == "delete"

    def test_a_memory_database_is_not_reported(self) -> None:
        """The suite opens several, and they can never be in WAL. A
        warning everybody learns to ignore is worse than none."""
        connection = sqlite3.connect(":memory:")
        try:
            assert ensure_wal(connection) is None
        finally:
            connection.close()

    def test_a_pragma_never_breaks_a_startup(self) -> None:
        """Whatever it cannot ask, it must not take startup down with
        it."""

        class Refuses:
            def execute(self, _sql: str) -> Any:
                raise RuntimeError("no pragmas here")

        assert ensure_wal(Refuses()) is None


def test_the_retry_helper_is_gone() -> None:
    """#158: the engine handles the lock once, so nothing in the app may
    know that SQLite has one writer.

    USE, not mentions: ``db.py`` keeps a comment saying what used to be
    there and why it went, which is the note that stops somebody adding
    it back the next time a write looks flaky.
    """
    import re

    import app.core.db as db_module

    assert not hasattr(db_module, "retry_on_locked")

    used = re.compile(r"retry_on_locked\s*\(|import[^\n]*\bretry_on_locked\b")
    offenders = [
        f"{path}:{number}"
        for path in Path("app").rglob("*.py")
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if used.search(line)
    ]
    assert not offenders, f"still wrapping writes by hand: {offenders}"
