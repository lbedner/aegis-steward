"""
Database backup service for aegis-steward.

Provides database backup functionality for scheduled backup jobs.
Included when scheduler and database components are both present.

"""

import asyncio
from datetime import datetime
from pathlib import Path
import sqlite3

from app.core.config import settings
from app.core.db import DATABASE_PATH
from app.core.log import logger

# Backup file naming pattern
BACKUP_FILE_PREFIX = "database_backup_"

BACKUP_FILE_SUFFIX = ".db"
BACKUP_FILE_PATTERNS = (f"{BACKUP_FILE_PREFIX}*{BACKUP_FILE_SUFFIX}",)

# Kept for callers that imported the single-pattern name.
BACKUP_FILE_PATTERN = BACKUP_FILE_PATTERNS[0]


def _copy_database(source: Path, destination: Path) -> None:
    """A consistent copy of a live SQLite database.

    NOT shutil.copy2. Under WAL a commit lives in the -wal until
    something checkpoints it back, and copying the main file alone
    silently produces a backup missing the newest writes - the exact
    ones anybody restoring is reaching for. On a database young enough
    that even its schema is still in the log, the copy comes back with
    NO TABLES AT ALL: a backup that opens cleanly and contains nothing.

    SQLite's own backup API reads through the log and takes a consistent
    snapshot while other connections keep writing, which is the whole
    reason it exists. It is also the right way to RESTORE: writing bytes
    over a database that still has a -wal beside it leaves the old log to
    be replayed on top of the new contents.

    Blocking, so callers run it off the loop.
    """
    live = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        copy = sqlite3.connect(destination)
        try:
            live.backup(copy)
        finally:
            copy.close()
    finally:
        live.close()


def backup_dir() -> Path:
    """The directory dumps are written to and pruned from.

    Configurable so deployments can point it at a mounted absolute path.
    Left relative it resolves against the process cwd, which inside a
    container is the writable layer: the dumps would then be destroyed by
    the next container recreation, i.e. every deploy.
    """
    return Path(settings.DATABASE_BACKUP_DIR)


def _list_backups(directory: Path) -> list[Path]:
    """Every backup file in ``directory``, newest first, all formats."""
    files = [
        f
        for pattern in BACKUP_FILE_PATTERNS
        for f in directory.glob(pattern)
        if f.is_file()
    ]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def _copy_database(source: Path, destination: Path) -> None:
    """A consistent copy of a live SQLite database.

    NOT ``shutil.copy2``. Under WAL a commit lives in the ``-wal`` until
    something checkpoints it back, and copying the main file alone
    silently produces a backup missing the newest writes - the exact
    ones anybody restoring is reaching for. On a database young enough
    that even its schema is still in the log, the copy comes back with
    no tables at all, which is what this looked like when it was found:
    a backup that opens cleanly and contains nothing.

    SQLite's own backup API reads through the log and takes a consistent
    snapshot while other connections keep writing, which is the whole
    reason it exists. Blocking, so callers run it off the loop.
    """
    live = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        copy = sqlite3.connect(destination)
        try:
            live.backup(copy)
        finally:
            copy.close()
    finally:
        live.close()


async def backup_database_job() -> None:
    """
    Scheduled database backup job.


    Creates a consistent snapshot of the SQLite database with timestamp.

    Keeps the last ``DATABASE_BACKUP_KEEP`` backups to bound disk usage.
    """
    # Ensure backup directory exists. parents=True because a configured
    # absolute path may be more than one level deep.
    directory = backup_dir()
    directory.mkdir(parents=True, exist_ok=True)

    # Create timestamped backup filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"{BACKUP_FILE_PREFIX}{timestamp}{BACKUP_FILE_SUFFIX}"
    backup_path = directory / backup_filename

    # A missing source means nothing was backed up, which is a failure
    # the operator needs to see -- don't swallow it.
    if not Path(DATABASE_PATH).exists():
        raise RuntimeError(f"Database file not found for backup: {DATABASE_PATH}")
    await asyncio.to_thread(_copy_database, Path(DATABASE_PATH), backup_path)
    logger.info(f"Database backup created: {backup_path}")

    # Prune old backups. Only reached on a successful backup, so a failed
    # run never deletes known-good backups.
    await _cleanup_old_backups(directory)


async def _cleanup_old_backups(directory: Path, keep_count: int | None = None) -> None:
    """
    Remove old backup files, keeping only the most recent ones.

    Args:
        directory: Directory containing backup files
        keep_count: Number of recent backups to keep (defaults to settings)
    """
    if keep_count is None:
        keep_count = settings.DATABASE_BACKUP_KEEP
    try:
        # One retention window across every format -- keeping N of each
        # would double the footprint the setting is meant to bound.
        backup_files = _list_backups(directory)

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
        directory = backup_dir()
        backup_path = directory / backup_filename

        if not backup_path.exists():
            logger.error(f"Backup file not found: {backup_path}")
            return False

        # Keep what is being replaced. Through the same consistent copy
        # as the scheduled job: this is the one somebody reaches for
        # when the restore turns out to have been a mistake, so it is
        # the last one that may quietly be missing the newest writes.
        current_db = Path(DATABASE_PATH)
        if current_db.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            current_backup = (
                directory / f"pre_restore_backup_{timestamp}{BACKUP_FILE_SUFFIX}"
            )
            await asyncio.to_thread(_copy_database, current_db, current_backup)
            logger.info(f"Created pre-restore backup: {current_backup}")

        # Restore from backup
        # Restore THROUGH SQLite rather than over the file: bytes written
        # onto a database that still has a -wal beside it leave the old
        # log to be replayed on top of the new contents, which is not a
        # failed restore but a corrupted one.
        await asyncio.to_thread(_copy_database, backup_path, Path(DATABASE_PATH))
        logger.info(f"Database restored from backup: {backup_filename}")
        return True

    except Exception as e:
        logger.error(f"Database restore failed: {e}")
        return False
