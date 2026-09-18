# app/core/db.py
"""
Database configuration and session management.

This module provides SQLite database connectivity using SQLModel and SQLAlchemy.
Includes proper session management with transaction handling and foreign key support.
"""

from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.log import logger


# Extract database file path from URL for backup operations
def _extract_database_path(database_url: str) -> str:
    """Extract the file path from a SQLite database URL."""
    parsed = urlparse(database_url)
    if parsed.scheme == "sqlite":
        # Handle both sqlite:/// and sqlite:// formats
        path = parsed.path
        if path.startswith("/") and len(parsed.netloc) == 0:
            # sqlite:///./path/file.db -> ./path/file.db
            return path[1:]
        elif parsed.netloc == "" and not path.startswith("/"):
            # sqlite://./path/file.db -> ./path/file.db
            return path
        else:
            # sqlite:///absolute/path/file.db -> /absolute/path/file.db
            return path
    else:
        raise ValueError(f"Unsupported database URL scheme: {parsed.scheme}")


DATABASE_PATH = _extract_database_path(settings.DATABASE_URL)


# Create SQLite engine with proper configuration (sync)
# NullPool disables connection pooling - each query gets a fresh connection
# This ensures the server sees changes made by external processes (e.g., CLI)
engine = create_engine(
    settings.DATABASE_URL,
    connect_args=settings.DATABASE_CONNECT_ARGS,
    echo=settings.DATABASE_ENGINE_ECHO,
    poolclass=NullPool,
)


# Create async engine for non-blocking operations
def _get_async_database_url(database_url: str) -> str:
    """Convert sync database URL to async version."""

    if database_url.startswith("sqlite:///"):
        return database_url.replace("sqlite:///", "sqlite+aiosqlite:///")
    elif database_url.startswith("sqlite://"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://")

    # For other database types, return as-is and let SQLAlchemy handle it
    return database_url


async_engine = create_async_engine(
    _get_async_database_url(settings.DATABASE_URL),
    echo=settings.DATABASE_ENGINE_ECHO,
    poolclass=NullPool,
    connect_args=settings.DATABASE_CONNECT_ARGS,
)


# SQLite has one writer at a time. Every connection, sync and async, waits
# its turn rather than failing at once: the conversation store and the
# usage recorder write through this sync engine at the end of a chat turn
# while a tool's session, the icon fill or the scheduler may hold the
# lock, and without the timeout that save failed with "database is
# locked" after the answer had already streamed.
SQLITE_BUSY_TIMEOUT_MS = 30000


def apply_sqlite_pragmas(dbapi_connection: Any) -> None:
    """The pragmas every SQLite connection opens with, sync and async.

    ``foreign_keys`` because SQLite ships them off, and the finance schema
    is nothing without them.

    ``busy_timeout`` because SQLite has one writer at a time and every
    connection should wait its turn rather than fail at once.

    NOT ``journal_mode``, even though WAL is exactly what this stack now
    wants. That pragma ANSWERS - it returns the mode - and a row left
    unfetched inside the connect hook is how a session that has not run
    a query yet fails to commit. It is not enough to close the cursor
    either: under aiosqlite ``close()`` is a coroutine, so a synchronous
    call returns an un-awaited object and closes nothing.

    The mode lives in the FILE and survives every restart, so it is set
    once at startup by ``ensure_wal`` rather than on every connect.
    """
    dbapi_connection.execute("PRAGMA foreign_keys=ON")
    dbapi_connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")


# There used to be a ``retry_on_locked`` here, and twelve call sites
# wrapped in it, because a deferred transaction that reads and then
# writes has to UPGRADE its lock and SQLite fails that upgrade at once.
# The engine takes the write lock up front now (see
# ``_async_sqlite_emit_begin``), so there is no upgrade to fail and
# ``busy_timeout`` covers the wait. Nothing outside this module should
# know SQLite has one writer; if that knowledge starts reappearing at
# call sites, the pragmas above are the thing to check.


def ensure_wal(dbapi_connection: Any) -> str | None:
    """Put the file in WAL, once, and say loudly if it would not go.

    Setting and checking are one call on purpose. The old version could
    only WARN, because across a bind mount WAL was the thing to avoid
    and ``journal_mode`` needs exclusive access to change. On a named
    volume the app can simply ask for the mode it needs, and a guard
    that reports a problem it could have fixed is a guard nobody acts
    on.

    Called once at startup rather than per connection. The mode lives in
    the FILE and persists, and this pragma answers with a row - which
    inside the connect hook is how a session that has not run a query
    yet fails to commit. See ``apply_sqlite_pragmas``.

    An in-memory database reports ``memory`` and can never be in WAL.
    That is not drift to report: the suite opens several, and a warning
    everybody learns to ignore is worse than no warning.

    Returns the mode when it is NOT what was asked for, so a caller can
    act; silence is what let the old configuration drift for weeks.
    """
    try:
        cursor = dbapi_connection.execute("PRAGMA journal_mode=WAL")
        mode = cursor.fetchone()[0]
        cursor.close()
    except Exception:  # noqa: BLE001 - a pragma must never break startup
        return None
    found = str(mode).lower()
    if found in ("wal", "memory"):
        return None
    logger.warning(
        "SQLite would not go into WAL mode, which this stack now requires: "
        "the engine takes the write lock up front, and without WAL every "
        "read queues behind every write. Something else holds this file - "
        "stop the stack and run PRAGMA journal_mode=WAL.",
        journal_mode=mode,
    )
    return found


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:
    apply_sqlite_pragmas(dbapi_connection)


# pysqlite's legacy transaction sniffing implicitly COMMITs the open
# transaction before emitting SAVEPOINT, silently breaking nested-transaction
# (begin_nested) semantics that the finance sync relies on for per-connection
# isolation. The documented recipe: disable the driver's implicit BEGIN/COMMIT
# and emit BEGIN ourselves.
@event.listens_for(async_engine.sync_engine, "connect")
def _async_sqlite_take_over_transactions(
    dbapi_connection: Any, connection_record: Any
) -> None:
    dbapi_connection.isolation_level = None
    apply_sqlite_pragmas(dbapi_connection)


@event.listens_for(async_engine.sync_engine, "begin")
def _async_sqlite_emit_begin(conn: Any) -> None:
    """BEGIN IMMEDIATE: take the write lock up front, where waiting works.

    A bare BEGIN is DEFERRED. SQLite takes no lock until the first write
    and then has to UPGRADE, and an upgrade that finds another writer
    fails with "database is locked" AT ONCE. ``busy_timeout`` does not
    apply to it, deliberately: a transaction that already holds a read
    lock while waiting for a write lock is how two of them deadlock, so
    SQLite refuses to enter that state rather than hang in it.

    So the 30-second timeout was never reached, and measurably so - the
    deferred path gives up in a tenth of a millisecond without ever
    consulting it. It cost a turn: the answer streamed, the proposal
    landed, and then ``save_memory``, a second session opened inside the
    same turn, died with the fact already spoken aloud.

    IMMEDIATE has no upgrade to fail, so the timeout finally applies. It
    was unaffordable while the file was bind-mounted: without WAL it
    makes every transaction a writer, reads included, and four
    containers sharing one rollback-journal file serialise into a hang.
    Under WAL a reader never queues behind the writer, so the cost is
    gone - which is what the move onto a named volume bought, and the
    only reason this line can read this way.

    One writer at a time is still true. It is now handled here, once,
    instead of at every call site that happened to read before writing.
    """
    conn.exec_driver_sql("BEGIN IMMEDIATE")


# Configure session factory with SQLModel Session (sync)
SessionLocal = sessionmaker(
    class_=Session, bind=engine, autoflush=False, autocommit=False
)

# Configure async session factory using SQLModel's AsyncSession
AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@contextmanager
def db_session(autocommit: bool = True) -> Generator[Session]:
    """
    Database session context manager with automatic transaction handling.

    Args:
        autocommit: Whether to automatically commit the transaction on success

    Yields:
        Session: Database session instance

    Example:
        with db_session() as session:
            # Your database operations here
            result = session.query(MyModel).first()
    """
    db_session: Session = SessionLocal()
    try:
        yield db_session
        if autocommit:
            db_session.commit()
    except Exception:
        db_session.rollback()
        raise
    finally:
        db_session.close()


@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession]:
    """
    Async database session context manager with automatic transaction handling.

    Yields:
        AsyncSession: Async database session instance

    Example:
        async with get_async_session() as session:
            # Your async database operations here
            result = await session.exec(select(MyModel))
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def init_database() -> Path:
    """
    Initialize the database by creating tables and ensuring directory structure.


    This function:
    1. Creates the database directory if it doesn't exist
    2. Creates all tables defined by SQLModel models
    3. Logs the initialization status

    """

    try:
        # The path comes from the ENGINE, not from DATABASE_PATH: tables are
        # created through the engine, so reporting the module constant
        # names a database this call may never have touched. The test suite
        # redirects the engine to a temp file, and for a long time this line
        # kept naming the developer's live ledger while writing elsewhere -
        # the one signal that would have shown the leak, saying the wrong
        # thing in both directions.
        db_path = Path(str(engine.url.database or DATABASE_PATH))
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # Create all tables
        SQLModel.metadata.create_all(engine)

        if db_path.exists():
            logger.info(f"Database initialized: {db_path}")
        else:
            logger.info(f"Database will be created on first use: {db_path}")

        return db_path

    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise


# ---------------------------------------------------------------------
# FastAPI-flavored session generators
#
# These live with the engine factories rather than under
# ``app/components/backend/api/deps.py`` so per-service deps modules
# (``app.services.<svc>.deps``) can import them without going through
# the api shim — the api shim imports BACK from the per-service deps,
# which would otherwise be a circular import.
#
# The functions are plain generators; FastAPI consumers wire them via
# ``Depends(get_async_db)`` at the call site.
# ---------------------------------------------------------------------


def get_db() -> Generator[Session]:
    """Yield a synchronous SQLModel session, closing it on exit."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_db() -> AsyncGenerator[AsyncSession]:
    """Yield an async SQLModel session, committing on success and rolling
    back on any exception."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
