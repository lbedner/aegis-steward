"""
Database health check dispatcher for aegis-steward.

Imports the appropriate database health check implementation based on
the configured database engine (SQLite or PostgreSQL).
"""

from app.services.system.health_db_sqlite import check_database_health

__all__ = ["check_database_health"]
