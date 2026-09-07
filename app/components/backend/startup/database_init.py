"""
Database initialization startup hook.

Ensures directory structure exists and creates tables
when the backend starts up (only when database component is included).
"""

from pathlib import Path

from app.components.backend.startup.schema_recovery import (
    _existing_tables_by_schema,
)
from app.core.log import logger
from app.services.scheduler.models import JobExecution  # noqa: F401


def _check_schema_mismatch() -> None:
    """
    Detect missing columns/tables and warn user.

    Compares SQLModel metadata (what the code expects) against
    the actual database schema. Only checks tables registered
    in SQLModel metadata — user-created tables are ignored.
    """
    try:
        from sqlalchemy import inspect as sa_inspect
        from sqlmodel import SQLModel

        from app.core.db import engine

        inspector = sa_inspect(engine)
        existing_tables = _existing_tables_by_schema(inspector)

        # Only check tables in SQLModel metadata (our models)
        expected_tables = set(SQLModel.metadata.tables.keys())
        missing_tables = expected_tables - existing_tables - {"alembic_version"}

        # Check columns for tables that exist in both
        missing_columns: dict[str, set[str]] = {}
        for table_key in expected_tables & existing_tables:
            table_obj = SQLModel.metadata.tables[table_key]
            model_cols = {c.name for c in table_obj.columns}
            db_cols = {
                c["name"]
                for c in inspector.get_columns(table_obj.name, schema=table_obj.schema)
            }
            missing = model_cols - db_cols
            if missing:
                missing_columns[table_key] = missing

        if missing_tables or missing_columns:
            logger.error("=" * 60)
            logger.error("SCHEMA MISMATCH DETECTED")
            logger.error(
                "Your database is missing columns/tables expected by the code."
            )
            if missing_tables:
                logger.error(f"  Missing tables: {', '.join(sorted(missing_tables))}")
            for table, cols in sorted(missing_columns.items()):
                logger.error(
                    f"  Missing columns in '{table}': {', '.join(sorted(cols))}"
                )
            logger.error("")
            logger.error(
                "  Fix: run 'make migrate-fix' to auto-generate a safe migration"
            )
            logger.error("=" * 60)

    except Exception as e:
        logger.debug(f"Schema mismatch check skipped: {e}")


async def startup_database_init() -> None:
    """
    Initialize database and run migrations.

    This hook runs when the backend starts to:
    1. Ensure database directory exists and create tables

    """
    try:
        # Ensure database directory exists
        from app.core.db import DATABASE_PATH

        db_path = Path(DATABASE_PATH)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # Create tables via SQLModel (no Alembic needed for SQLite)
        from sqlmodel import SQLModel

        from app.core.db import engine

        SQLModel.metadata.create_all(engine)
        logger.info("Database tables created/verified (SQLite)")

        # Check for schema mismatches (e.g., after aegis update added new columns)
        _check_schema_mismatch()

        # Verify database connectivity
        try:
            from sqlalchemy import inspect
            from sqlmodel import text

            from app.core.db import db_session

            with db_session(autocommit=False) as session:
                # Basic connectivity check
                session.exec(text("SELECT 1"))

                inspector = inspect(session.connection())
                table_names = inspector.get_table_names()

                logger.info(f"Database ready with {len(table_names)} tables")

        except Exception as e:
            logger.warning(f"Database verification failed: {e}")
            # Don't fail startup - let the app run and show clear errors

        # Seed finance reference data: currencies + CSV import profiles
        # (idempotent). Import profiles are what CSV header-detection matches
        # against, so a fresh DB can import Chase/AMEX files immediately.
        try:
            from app.core.db import db_session as get_db_session
            from app.services.finance.seeds.seed import seed_finance_tables

            with get_db_session() as session:
                seed_finance_tables(session)
            logger.info("Finance seed data verified")
        except Exception as e:
            logger.warning(f"Finance seed failed: {e}")

        # Seed the agent registry: agents, memory modules, and one tool row
        # per registered tool. Tool rows are what grants attach to by name,
        # so an unseeded registry leaves every agent without its tools.
        try:
            from app.components.backend.startup.agent_registry import (
                seed_agent_registry,
            )

            counts = seed_agent_registry()
            logger.info(f"Agent registry verified: {counts}")
        except Exception as e:
            logger.warning(f"Agent registry seed failed: {e}")

        # Remote catalog and local Ollama tags, each only when missing.
        try:
            from app.components.backend.startup.llm_catalog import seed_llm_catalog

            await seed_llm_catalog()
        except Exception as e:
            logger.warning(f"LLM catalog startup skipped: {e}")

    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise


# Export the startup hook function
startup_hook = startup_database_init
