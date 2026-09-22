"""Test engines shaped like the real one.

``app.core.db`` emits ``BEGIN IMMEDIATE`` so ``busy_timeout`` applies -
a deferred BEGIN fails its lock upgrade at once instead of waiting - and
puts the file in WAL so readers never queue behind the writer. The
suite's engines did none of that, so an open session in a test was not
the write lock and no test could reproduce a lock-contention bug. Two
shipped in a week.

The shape is DERIVED from ``app.core.db``'s own functions, never
restated: a change to the real pragmas that the suite does not get is
how the suite goes back to lying.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

# Short enough to assert against. A test proving the lock is held must
# not take the production thirty seconds to say so.
IMPATIENT_BUSY_TIMEOUT_MS = 400

Attach = Callable[[Any], None]


def shape_like_production(
    engine: AsyncEngine,
    *,
    attach: Attach | None = None,
    busy_timeout_ms: int | None = None,
) -> None:
    """Give ``engine`` production's transaction behaviour.

    ``attach`` adds the schema databases, which differ per fixture (files
    beside the main one, or ``:memory:``). ``busy_timeout_ms`` overrides
    the real wait for tests that must not spend it.
    """
    from app.core.db import apply_sqlite_pragmas

    @event.listens_for(engine.sync_engine, "connect")
    def _connect(dbapi_connection: Any, connection_record: Any) -> None:
        # Take over transaction control, as production does: pysqlite's
        # implicit BEGIN/COMMIT sniffing commits before SAVEPOINT, which
        # breaks begin_nested() and leaks rows across the per-test
        # rollback.
        dbapi_connection.isolation_level = None
        apply_sqlite_pragmas(dbapi_connection)
        # WAL through a cursor that is CLOSED, not through ``ensure_wal``:
        # that one reads the mode back with ``cursor.fetchone()``, which
        # under aiosqlite is a coroutine nobody awaits - so it swallows
        # its own failure and leaves the statement unfinished, and the
        # next COMMIT dies with "SQL statements in progress". Production
        # calls it once at startup on a SYNC connection, which is why it
        # has never had to care.
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        if busy_timeout_ms is not None:
            cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        cursor.close()
        if attach is not None:
            attach(dbapi_connection)

    @event.listens_for(engine.sync_engine, "begin")
    def _begin(conn: Any) -> None:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
