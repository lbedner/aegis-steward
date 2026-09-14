# app/core/db.py
"""
Database configuration and session management.

This module provides SQLite database connectivity using SQLModel and SQLAlchemy.
Includes proper session management with transaction handling and foreign key support.
"""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlparse

from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
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

    NOT ``journal_mode=WAL``, though it is the usual answer to a writer
    that starves readers: WAL coordinates its readers through shared
    memory mapped from a ``-shm`` file, which is only coherent between
    processes on one machine. The dev stack bind-mounts this file from
    the host into several containers and the CLI opens it from the host
    as well, so those processes do not share memory and WAL would risk
    the ledger. It belongs with a database the container boundary does
    not cross (a named volume), or with Postgres.
    """
    dbapi_connection.execute("PRAGMA foreign_keys=ON")
    dbapi_connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")


# A deferred transaction that reads and then writes has to UPGRADE its
# lock, and SQLite fails that upgrade AT ONCE rather than waiting: a
# transaction holding a read lock while waiting for a write lock is how
# two of them deadlock, so ``busy_timeout`` deliberately does not cover
# it. Taking the write lock up front instead (BEGIN IMMEDIATE) makes
# every read a writer, which on a stack of four containers sharing one
# file is a hang. So the upgrade is retried where it happens.
T = TypeVar("T")

LOCK_RETRIES = 4
LOCK_BACKOFF_SECONDS = 0.25


async def retry_on_locked(write: Callable[[], Awaitable[T]]) -> T:
    """Run an async write, retrying the lock-upgrade failure.

    For the turn that reads and then writes - a chat answer that saves a
    memory, a proposal that files a change - where the read has already
    taken a shared lock and the write cannot have it. Nothing else
    retries: a lock held for longer than this is a problem to see, not
    to paper over.
    """
    for attempt in range(LOCK_RETRIES):
        try:
            return await write()
        except OperationalError as exc:
            if "database is locked" not in str(exc) or attempt == LOCK_RETRIES - 1:
                raise
            await asyncio.sleep(LOCK_BACKOFF_SECONDS * (attempt + 1))
    raise AssertionError("unreachable")


def warn_if_wal(dbapi_connection: Any) -> str | None:
    """Say so, loudly, when the file is in WAL after all.

    The mode above is a decision this module makes and cannot enforce:
    ``journal_mode`` lives in the FILE, survives every restart, and
    changing it needs exclusive access, so a connection that finds WAL
    cannot simply turn it off. It is also how a ledger ends up running
    for weeks in the configuration its own code says would risk it -
    found exactly that way, after a night of "database is locked" with
    five containers and a host CLI sharing one bind-mounted file.

    Called once at startup rather than per connection: a pragma read
    leaves a cursor open, and doing that inside the connect hook is how
    a session that has not run a query yet fails to commit.

    Returns the mode when it is not what was intended, so a caller can
    act; silence is what let it drift in the first place.
    """
    try:
        cursor = dbapi_connection.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        cursor.close()
    except Exception:  # noqa: BLE001 - a pragma read must never break startup
        return None
    if str(mode).lower() != "wal":
        return None
    logger.warning(
        "SQLite is in WAL mode, which this stack does not intend: its -shm "
        "coordination is not coherent across a bind mount, and several "
        "containers plus the host CLI share this file. Stop the stack and "
        "run PRAGMA journal_mode=DELETE.",
        journal_mode=mode,
    )
    return str(mode)


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
    """A deferred BEGIN, with the upgrade failure handled elsewhere.

    A bare BEGIN is DEFERRED: SQLite takes no lock until the first write
    and then has to UPGRADE. If another writer holds the lock at that
    moment the upgrade fails with "database is locked" AT ONCE -
    ``busy_timeout`` deliberately does not apply to it, because a
    transaction that already holds a read lock and waits for a write
    lock is how two of them deadlock.

    So the 30-second timeout above was never reached. It cost a turn
    live: the answer streamed, the proposal landed, and then
    ``save_memory`` - a second session opened inside the same turn -
    died on "database is locked" with the fact already spoken aloud.

    IMMEDIATE takes the write lock up front, where waiting is safe and
    the timeout DOES apply. It was tried, and it is NOT the answer here:
    it makes every transaction a writer, including reads, and this stack
    runs four containers and a host CLI against one file. Under WAL that
    was survivable because readers proceed anyway; with a rollback
    journal - which this stack intends, see ``apply_sqlite_pragmas`` -
    every read queues behind every write and the app deadlocks itself
    into a hang.

    So: a deferred BEGIN, and the upgrade failure is handled where it
    actually happens - ``retry_on_locked`` around the write - rather than
    by making the whole application single-file-serial.
    """
    conn.exec_driver_sql("BEGIN")


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
