"""The system service's scheduled jobs (``app.core.schedule``). A backup
needs a database to copy and a scheduler to run it."""

from app.core.schedule import LONG_RUNNING, ServiceJob
from app.services.system.backup import backup_database_job

JOBS: tuple[ServiceJob, ...] = (
    ServiceJob(
        backup_database_job,
        "database_backup",
        "Daily Database Backup",
        {"trigger": "cron", "hour": 2, "minute": 0},
        timeout=LONG_RUNNING,
    ),
)
