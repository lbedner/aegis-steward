"""
Database health check dispatcher for aegis-steward.

Runs the check for the configured engine (SQLite or PostgreSQL) and adds
what ``app.core.db_activity`` recorded: slow transactions and lock waits,
which the Database pages show.
"""

from app.core import db_activity
from app.services.system.health_db_sqlite import (
    check_database_health as _check_engine,
)
from app.services.system.models import ComponentStatus

__all__ = ["check_database_health"]


async def check_database_health() -> ComponentStatus:
    """The engine's check, with the recent database activity."""
    status = await _check_engine()
    status.metadata = (status.metadata or {}) | {"activity": await db_activity.recent()}
    return status
