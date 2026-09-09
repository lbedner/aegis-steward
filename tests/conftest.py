"""
Pytest configuration and fixtures for test suite.

Provides common fixtures and configuration for all tests.
"""

from collections.abc import AsyncGenerator, Generator
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

# Before any app import reads settings: a developer .env with a real
# LOGFIRE_TOKEN must never arm instrumentation (or ship test spans).
os.environ["LOGFIRE_TOKEN"] = ""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest  # noqa: E402
from sqlalchemy import create_engine, event
from sqlalchemy.engine.base import Engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

# Import AI models to register them with SQLModel metadata. The agents and
# llm subpackages each import every model module they own, so these two
# names pull in the whole set.
from app.services.ai.models.agents import (  # noqa: F401
    Agent,
    AgentTool,
    AgentUserMemory,
    MemoryModule,
    Tool,
)
from app.services.ai.models.llm import (  # noqa: F401
    LargeLanguageModel,
    LLMActiveSelection,
    LLMDeployment,
    LLMModality,
    LLMOrg,
    LLMPrice,
    LLMUsage,
)

# Import finance models to register them with SQLModel metadata
from app.services.finance.models import (  # noqa: F401
    FinanceAccount,
    FinanceAttachment,
    FinanceBalanceSnapshot,
    FinanceBudget,
    FinanceBudgetCategory,
    FinanceCategory,
    FinanceCategoryAlias,
    FinanceConnection,
    FinanceCurrency,
    FinanceFxRate,
    FinanceHolding,
    FinanceImportBatch,
    FinanceImportBatchRow,
    FinanceImportProfile,
    FinanceInsight,
    FinanceInstitution,
    FinanceLiabilityDetail,
    FinanceMerchant,
    FinanceNetWorthSnapshot,
    FinanceRecurringStream,
    FinanceRule,
    FinanceSecurity,
    FinanceSecurityPrice,
    FinanceSpendingBaseline,
    FinanceTag,
    FinanceTrade,
    FinanceTransaction,
    FinanceTransactionChangelog,
    FinanceTransactionSplit,
    FinanceTransactionTag,
    FinanceTransfer,
    FinanceValuation,
    FinanceWebhookEvent,
)

# Import scheduler models to register them with SQLModel metadata
from app.services.scheduler.models import JobExecution  # noqa: F401

# Add project root to Python path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.integrations.main import create_integrated_app


# Swap the module-level ``cache`` singleton to the in-memory dict
# backend for the whole test session. The Redis-backed singleton
# binds its async connection pool to the event loop alive when it
# was constructed (at import time). pytest-asyncio creates a fresh
# loop for every test, so the first test's Redis client becomes
# unusable for every subsequent test. Production isn't affected
# (uvicorn keeps one loop for the server's lifetime); only the
# test suite needs this swap. Dict-mode keeps the same async API,
# so call sites don't change.
@pytest.fixture(autouse=True, scope="session")
def _use_dict_backed_cache():
    import app.core.cache as cache_module

    original = cache_module.cache
    cache_module.cache = cache_module.CacheService()
    yield
    cache_module.cache = original


@pytest.fixture
def app() -> FastAPI:
    """Create a configured FastAPI app instance for testing."""
    return create_integrated_app()


@pytest.fixture
def client(app: FastAPI) -> Generator[TestClient]:
    """Create a test client for the FastAPI app."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client_with_db(app: FastAPI, db_session: Session) -> Generator[TestClient]:
    """Create a test client with database dependency override."""
    from app.core.db import get_db

    def get_test_db() -> Generator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = get_test_db

    with TestClient(app) as test_client:
        yield test_client

    # Clean up dependency override
    app.dependency_overrides.clear()


@pytest.fixture(scope="session")
def engine() -> Engine:
    """
    Create in-memory SQLite database engine for tests.

    Uses :memory: database that exists only in RAM for maximum speed
    and perfect test isolation. Each test session gets a fresh database.

    Returns:
        SQLAlchemy Engine connected to in-memory SQLite database
    """
    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,  # Set to True for SQL debugging
        connect_args={"check_same_thread": False},  # Allow multi-threaded access
    )

    # Non-default schemas our models declare (e.g. the scheduler component's
    # ``scheduler`` schema on Postgres projects). SQLite has no schemas, so
    # attach an in-memory database under each name to make schema-qualified
    # tables resolvable in the test database.
    schema_names = {
        table.schema for table in SQLModel.metadata.tables.values() if table.schema
    }

    # Critical: Enable foreign key constraints in SQLite
    # SQLite has foreign keys disabled by default for backwards compatibility
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        for schema_name in schema_names:
            cursor.execute(f"ATTACH DATABASE ':memory:' AS {schema_name}")
        cursor.close()

    # Create all tables once per test session
    SQLModel.metadata.create_all(engine)

    return engine


@pytest.fixture(scope="function")
def db_session(engine: Engine) -> Generator[Session]:
    """
    Provide transactional database session with automatic rollback.

    Each test gets a fresh transaction that's rolled back after the test,
    ensuring perfect isolation between tests. Uses the same transaction
    pattern as PostgreSQL for consistency.

    Args:
        engine: Database engine from session-scoped fixture

    Yields:
        SQLModel Session for database operations
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(connection)

    yield session

    # Clean up: rollback transaction and close connection
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture(scope="session")
async def app_owned_engine():
    """A database for sessions the APP opens for itself.

    Deliberately not ``async_engine``: that one is wrapped in a per-test
    transaction which is rolled back, and app-owned sessions commit. Put
    both on one connection and they fight over the same transaction.

    File-backed, not ``:memory:``. TestClient runs the app in its OWN
    event loop on a separate thread, so app-owned sessions reach this
    engine from a different thread than the one that built it. Every new
    connection to ``:memory:`` is a BRAND NEW EMPTY database, so a
    reconnect there loses every table - which surfaces much later as
    "no such table: agent", and only in long runs, because a short one
    never reconnects. A file is the same database from any thread.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="aegis-app-owned-db-"))
    schema_names = {
        table.schema for table in SQLModel.metadata.tables.values() if table.schema
    }
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_dir / 'app_owned.sqlite'}",
        echo=False,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def attach_schemas(dbapi_connection: Any, connection_record: Any) -> None:
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        # Files here too, for the same reason the main database is one.
        for schema_name in schema_names:
            cursor.execute(
                f"ATTACH DATABASE '{tmp_dir / (schema_name + '.sqlite')}' "
                f"AS {schema_name}"
            )
        cursor.close()

    @event.listens_for(engine.sync_engine, "begin")
    def emit_begin(conn: Any) -> None:
        conn.exec_driver_sql("BEGIN")

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    yield engine

    await engine.dispose()
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _no_production_storage(tmp_path: Path) -> Generator[None]:
    """Object storage (chat attachments) writes under ``STORAGE_ROOT``,
    which on the host is the repo's ``storage_data/``; a test that stores
    bytes must not leave them there. Each test gets its own directory."""
    from app.core.storage import FilesystemStorage, set_storage

    set_storage(FilesystemStorage(tmp_path / "storage"))
    yield
    set_storage(None)


@pytest.fixture(autouse=True)
def _no_production_database(app_owned_engine, monkeypatch):
    """Keep app-owned sessions off the real database.

    ``get_async_session()`` reads a module-level ``AsyncSessionLocal``.
    Overriding the FastAPI dependency does not touch it, so every caller
    that opens its own session - startup hooks, background tasks,
    ``resolve_agent()`` inside a chat - was talking to the database in
    ``DATABASE_URL`` while the suite ran.

    Two consequences. Tests read and write live data. And the production
    engine POOLS asyncpg connections, which pytest then hands to a fresh
    event loop each test; using a connection from a loop that did not
    create it raises "another operation is in progress" - failing tests
    that never touched the database themselves, only in a full run, and
    never in isolation.
    """
    import app.core.db as db_module

    monkeypatch.setattr(
        db_module,
        "AsyncSessionLocal",
        async_sessionmaker(
            app_owned_engine, class_=AsyncSession, expire_on_commit=False
        ),
    )
    # The SYNC factory too: the conversation store (``db_session()``) and
    # the usage recorder write through it, and with only the async one
    # redirected every chat test that persisted a turn wrote into the
    # database in ``DATABASE_URL`` (hundreds of "hello" conversations in a
    # developer's live file). Same temp file, so both views agree.
    monkeypatch.setattr(
        db_module,
        "SessionLocal",
        sessionmaker(bind=_sync_twin(app_owned_engine), class_=Session),
    )


def _sync_twin(async_engine: Any) -> Engine:
    """A sync engine on the app-owned test database file, with the same
    schema attachments and pragmas its async engine has."""
    path = async_engine.url.database
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False}
    )
    schema_names = {
        table.schema for table in SQLModel.metadata.tables.values() if table.schema
    }

    @event.listens_for(engine, "connect")
    def attach_schemas(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        for schema_name in schema_names:
            cursor.execute(
                f"ATTACH DATABASE '{Path(path).parent / (schema_name + '.sqlite')}' "
                f"AS {schema_name}"
            )
        cursor.close()

    return engine


@pytest.fixture(scope="session")
async def async_engine():
    """
    Create async in-memory SQLite database engine for async tests.

    Uses :memory: database that exists only in RAM for maximum speed
    and perfect test isolation. Each test session gets a fresh database.

    Returns:
        Async SQLAlchemy Engine connected to in-memory SQLite database
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,  # Set to True for SQL debugging
        connect_args={"check_same_thread": False},  # Allow multi-threaded access
    )

    # Attach an in-memory database for each non-default schema our models use
    # (mirrors the sync ``engine`` fixture). SQLite has no schemas, so without
    # this, create_all of a schema-qualified table (e.g. scheduler.job_execution
    # on Postgres projects) fails.
    schema_names = {
        table.schema for table in SQLModel.metadata.tables.values() if table.schema
    }

    @event.listens_for(engine.sync_engine, "connect")
    def attach_schemas(dbapi_connection: Any, connection_record: Any) -> None:
        # Match production transaction handling: pysqlite's implicit
        # BEGIN/COMMIT sniffing commits the open transaction before SAVEPOINT,
        # which both breaks begin_nested() semantics and leaks rows across the
        # per-test rollback isolation. Take over transaction control (paired
        # with the "begin" listener below).
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        # Match the sync engine: enforce foreign keys so async tests catch
        # FK violations that would otherwise pass here but fail in production.
        cursor.execute("PRAGMA foreign_keys=ON")
        for schema_name in schema_names:
            cursor.execute(f"ATTACH DATABASE ':memory:' AS {schema_name}")
        cursor.close()

    @event.listens_for(engine.sync_engine, "begin")
    def emit_begin(conn: Any) -> None:
        conn.exec_driver_sql("BEGIN")

    # Create all tables once per test session
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest.fixture(scope="function")
async def async_db_session(async_engine) -> AsyncGenerator[AsyncSession]:
    """
    Provide async transactional database session with automatic rollback.

    Each test gets a fresh transaction that's rolled back after the test,
    ensuring perfect isolation between tests. Uses async SQLite with aiosqlite.

    Args:
        async_engine: Async database engine from session-scoped fixture

    Yields:
        AsyncSession: SQLModel async session for database operations
    """
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        session_factory = async_sessionmaker(
            bind=connection, class_=AsyncSession, expire_on_commit=False
        )

        async with session_factory() as session:
            yield session

        # Clean up: rollback transaction
        await transaction.rollback()


@pytest.fixture
async def async_client_with_db(
    app: FastAPI, async_db_session: AsyncSession
) -> AsyncGenerator[TestClient]:
    """Create a test client with async database dependency override."""
    from app.core.db import get_async_db

    async def get_test_async_db() -> AsyncGenerator[AsyncSession]:
        yield async_db_session

    app.dependency_overrides[get_async_db] = get_test_async_db

    with TestClient(app) as test_client:
        yield test_client

    # Clean up dependency override
    app.dependency_overrides.clear()


@pytest.fixture
async def authenticated_client(
    async_client_with_db: TestClient,
    auth_headers: dict[str, str],
) -> TestClient:
    """``async_client_with_db`` with ``auth_headers`` pre-injected.

    Use this when a test needs "an authenticated request, don't care
    who" — write ``authenticated_client.get(url)`` instead of plumbing
    ``headers={"Authorization": ...}`` on every call. Eliminates the
    per-request boilerplate without hiding which user is acting (the
    seeded ``auth_user`` / ``admin_user`` is the actor).

    Auth-mode behavior:

    - ``include_auth_rbac``: signs requests as ``admin_user``.
    - ``include_auth``: signs requests as ``auth_user``.
    - no auth: ``auth_headers`` is ``{}``; this fixture is a passthrough.

    For tests that need multiple distinct identities or want to pin a
    specific user / role, skip this and compose ``user_factory`` +
    ``create_access_token`` inline so the identity choice is visible.
    """
    async_client_with_db.headers.update(auth_headers)
    return async_client_with_db


# ---------------------------------------------------------------------------
# Auth headers fixture chain — kept OUTSIDE the ``include_database`` block
# above so the no-op fallback is available even in stacks without a DB
# (e.g. ``worker``-only). ``test_worker_endpoints.py.jinja`` always asks
# for ``auth_headers``; without this it would error with "fixture not
# found" in DB-less stacks. The RBAC / basic-auth branches still need
# ``async_db_session`` to seed users, which only exists when
# ``include_database`` is true — the template never enables auth without
# a database, so those branches are safe to reference it.
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """No-op headers fixture for stacks without the auth service.

    Tests that share assertions with auth-enabled stacks can ask for
    ``auth_headers`` and get ``{}`` here; the endpoints aren't gated.
    """
    return {}


@pytest.fixture
def acting_owner_user_id() -> int | None:
    """No auth service, so owner-scoped rows carry no owner.

    The auth-stack counterpart returns the authenticated user's id; here
    ``None`` keeps seed helpers written once and correct in both stacks.
    """
    return None
