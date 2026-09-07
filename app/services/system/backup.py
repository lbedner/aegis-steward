"""
Database backup service for aegis-steward.

Provides database backup functionality for scheduled backup jobs.
Included when scheduler and database components are both present.
"""

from datetime import datetime
from pathlib import Path
import shutil

from app.core.db import DATABASE_PATH
from app.core.log import logger

# Backup file naming pattern

BACKUP_FILE_PATTERN = "database_backup_*.db"
BACKUP_FILE_PREFIX = "database_backup_"


async def backup_database_job() -> None:
    """
    Scheduled database backup job.


    Creates a backup of the SQLite database file with timestamp.

    Keeps the last 7 daily backups to prevent disk space issues.
    """
    # Ensure backup directory exists
    backup_dir = Path("backups")
    backup_dir.mkdir(exist_ok=True)

    # Create timestamped backup filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    backup_filename = f"{BACKUP_FILE_PREFIX}{timestamp}.db"
    backup_path = backup_dir / backup_filename

    # Copy the database file. A missing source means nothing was backed up,
    # which is a failure the operator needs to see -- don't swallow it.
    if not Path(DATABASE_PATH).exists():
        raise RuntimeError(f"Database file not found for backup: {DATABASE_PATH}")
    shutil.copy2(DATABASE_PATH, backup_path)
    logger.info(f"Database backup created: {backup_path}")

    # Prune old backups (keep last 7). Only reached on a successful backup, so
    # a failed run never deletes known-good backups.
    await _cleanup_old_backups(backup_dir)


async def _cleanup_old_backups(backup_dir: Path, keep_count: int = 7) -> None:
    """
    Remove old backup files, keeping only the most recent ones.

    Args:
        backup_dir: Directory containing backup files
        keep_count: Number of recent backups to keep
    """
    try:
        # Get all backup files sorted by modification time (newest first)
        backup_files = [f for f in backup_dir.glob(BACKUP_FILE_PATTERN) if f.is_file()]
        backup_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)

        # Remove old backups beyond keep_count
        old_backups = backup_files[keep_count:]
        for old_backup in old_backups:
            old_backup.unlink()
            logger.info(f"Removed old backup: {old_backup.name}")

        if old_backups:
            kept_count = min(len(backup_files), keep_count)
            logger.info(f"Cleaned up {len(old_backups)} old backups, kept {kept_count}")

    except Exception as e:
        logger.error(f"Backup cleanup failed: {e}")


async def restore_database_from_backup(backup_filename: str) -> bool:
    """
    Restore database from a backup file.

    Args:
        backup_filename: Name of the backup file to restore from

    Returns:
        True if restore was successful, False otherwise
    """
    try:
        backup_dir = Path("backups")
        backup_path = backup_dir / backup_filename

        if not backup_path.exists():
            logger.error(f"Backup file not found: {backup_path}")
            return False

        # Create backup of current database before restore
        current_db = Path(DATABASE_PATH)
        if current_db.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            current_backup = backup_dir / f"pre_restore_backup_{timestamp}.db"
            shutil.copy2(current_db, current_backup)
            logger.info(f"Created pre-restore backup: {current_backup}")

        # Restore from backup
        shutil.copy2(backup_path, DATABASE_PATH)
        logger.info(f"Database restored from backup: {backup_filename}")
        return True

    except Exception as e:
        logger.error(f"Database restore failed: {e}")
        return False
