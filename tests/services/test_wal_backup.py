"""A backup taken while the database is in WAL must carry the log.

``shutil.copy2`` copies the main file and nothing else. Under WAL a
commit lives in the ``-wal`` until a checkpoint folds it back, so a
plain file copy silently produces a backup missing the most recent
writes - exactly the ones somebody restoring would be looking for.
"""

from pathlib import Path
import sqlite3

import pytest


@pytest.mark.asyncio
async def test_a_backup_carries_writes_still_in_the_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "app.db"
    live = sqlite3.connect(source)
    live.execute("PRAGMA journal_mode=WAL").fetchone()
    live.execute("create table t (v text)")
    live.execute("insert into t values ('before')")
    live.commit()

    from app.services.system import backup as backup_module

    monkeypatch.setattr(backup_module, "DATABASE_PATH", str(source))
    monkeypatch.chdir(tmp_path)

    # A commit that is in the WAL and not yet checkpointed into the
    # main file. Hold the connection open so nothing checkpoints it.
    live.execute("insert into t values ('after')")
    live.commit()

    await backup_module.backup_database_job()

    copies = sorted((tmp_path / "backups").glob("database_backup_*.db"))
    assert len(copies) == 1, copies
    restored = sqlite3.connect(copies[0])
    try:
        rows = {r[0] for r in restored.execute("select v from t")}
    finally:
        restored.close()
        live.close()
    assert rows == {"before", "after"}, f"backup lost a committed write: {rows}"


@pytest.mark.asyncio
async def test_a_missing_database_is_a_failure_not_an_empty_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backup job that quietly succeeds on a database that is not
    there is how a week of backups turns out to be nothing."""
    from app.services.system import backup as backup_module

    monkeypatch.setattr(backup_module, "DATABASE_PATH", str(tmp_path / "gone.db"))
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError, match="not found"):
        await backup_module.backup_database_job()


@pytest.mark.asyncio
async def test_a_restore_does_not_leave_the_old_log_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writing bytes over a database that still has a ``-wal`` beside it
    leaves the old log to be replayed on top of the new contents. That
    is not a failed restore, it is a corrupted one."""
    from app.services.system import backup as backup_module

    backups = tmp_path / "backups"
    backups.mkdir()

    # A backup holding the row we want back.
    wanted = backups / "database_backup_20260101_000000.db"
    made = sqlite3.connect(wanted)
    made.execute("create table t (v text)")
    made.execute("insert into t values ('wanted')")
    made.commit()
    made.close()

    # A live database in WAL, mid-life, with its own uncheckpointed log.
    live_path = tmp_path / "app.db"
    live = sqlite3.connect(live_path)
    live.execute("PRAGMA journal_mode=WAL").fetchone()
    live.execute("create table t (v text)")
    live.execute("insert into t values ('doomed')")
    live.commit()
    live.close()

    monkeypatch.setattr(backup_module, "DATABASE_PATH", str(live_path))
    monkeypatch.chdir(tmp_path)

    assert await backup_module.restore_database_from_backup(wanted.name) is True

    restored = sqlite3.connect(live_path)
    try:
        rows = {r[0] for r in restored.execute("select v from t")}
    finally:
        restored.close()
    assert rows == {"wanted"}, f"the old database survived the restore: {rows}"

    # And the row that was replaced is still reachable.
    saved = sorted(backups.glob("pre_restore_backup_*.db"))
    assert len(saved) == 1, saved
    kept = sqlite3.connect(saved[0])
    try:
        assert {r[0] for r in kept.execute("select v from t")} == {"doomed"}
    finally:
        kept.close()
