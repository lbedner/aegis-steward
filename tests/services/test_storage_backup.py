"""The nightly backup also snapshots object storage.

Documents and attachments live under STORAGE_ROOT, not in the database,
so a database copy alone restores rows that point at files nobody kept.
"""

import os
from pathlib import Path
import sqlite3
import tarfile

import pytest

from app.core.config import settings
from app.services.system import backup as backup_module


@pytest.fixture
def stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "app.db"
    sqlite3.connect(db).close()
    store = tmp_path / "store"
    (store / "documents" / "ab").mkdir(parents=True)
    (store / "documents" / "ab" / "abc123").write_bytes(b"statement pdf")
    monkeypatch.setattr(backup_module, "DATABASE_PATH", str(db))
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(store))
    monkeypatch.setattr(settings, "DATABASE_BACKUP_DIR", str(tmp_path / "backups"))
    return tmp_path / "backups"


@pytest.mark.asyncio
async def test_the_job_snapshots_the_store_beside_the_database(stack: Path) -> None:
    await backup_module.backup_database_job()

    snapshots = list(stack.glob("storage_backup_*.tar"))
    assert len(snapshots) == 1, list(stack.iterdir())
    with tarfile.open(snapshots[0]) as tar:
        member = tar.extractfile("storage/documents/ab/abc123")
        assert member is not None
        assert member.read() == b"statement pdf"
    # The restore list stays database copies only.
    assert all(p.suffix == ".db" for p in backup_module.list_backups(stack))


@pytest.mark.asyncio
async def test_snapshots_share_the_database_retention(
    stack: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DATABASE_BACKUP_KEEP", 2)
    stack.mkdir()
    for day in (1, 2, 3):
        old = stack / f"storage_backup_2026010{day}_000000.tar"
        old.write_bytes(b"")
        os.utime(old, (day, day))

    await backup_module.backup_database_job()

    kept = sorted(p.name for p in stack.glob("storage_backup_*.tar"))
    assert len(kept) == 2, kept
    assert "storage_backup_20260103_000000.tar" in kept


@pytest.mark.asyncio
async def test_no_store_yet_is_not_a_failure(
    stack: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path / "never-written"))

    await backup_module.backup_database_job()

    assert list(stack.glob("database_backup_*.db"))
    assert not list(stack.glob("storage_backup_*"))


@pytest.mark.asyncio
async def test_a_bucket_is_not_archived(stack: Path) -> None:
    """A bucket keeps its own copies; only a filesystem store is tarred."""
    from app.core.storage import set_storage

    set_storage(object())  # type: ignore[arg-type]

    await backup_module.backup_database_job()

    assert list(stack.glob("database_backup_*.db"))
    assert not list(stack.glob("storage_backup_*"))
