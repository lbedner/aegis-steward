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


# Enable foreign key constraints for SQLite
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:
    """Enable foreign key constraints in SQLite."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


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
    # A chat run holds one connection while each tool call commits on its
    # own; without a timeout the second writer fails at once with
    # "database is locked" instead of waiting its turn.
    dbapi_connection.execute("PRAGMA busy_timeout=30000")


@event.listens_for(async_engine.sync_engine, "begin")
def _async_sqlite_emit_begin(conn: Any) -> None:
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


def init_database() -> None:
    """
    Initialize the database by creating tables and ensuring directory structure.


    This function:
    1. Creates the database directory if it doesn't exist
    2. Creates all tables defined by SQLModel models
    3. Logs the initialization status

    """

    try:
        # Ensure database directory exists
        db_path = Path(DATABASE_PATH)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # Create all tables
        SQLModel.metadata.create_all(engine)

        if db_path.exists():
            logger.info(f"Database initialized: {DATABASE_PATH}")
        else:
            logger.info(f"Database will be created on first use: {DATABASE_PATH}")

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
