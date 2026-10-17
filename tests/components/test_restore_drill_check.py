"""A restore drill's checks, run by the app's own code against a restored
copy (``deploy drill-check``, aegis-stack#1482): the database whole, the
app's startup bringing it up, every stored secret readable with this
``ENCRYPTION_KEY``, every stored file the rows name present."""

import json
from pathlib import Path
import sqlite3

import pytest

from app.components.deploy import drill
from app.core.cache import get_cache
from app.core.config import settings
from app.core.storage import content_key
from tests._cli import invoke

pytestmark = pytest.mark.queryspy(allow_n_plus_one=True)
STARTED = {"name": "startup", "ok": True, "said": ""}
# The real one: every other test stands in for the app's startup.
real_started = drill._started


def _database(path: Path) -> Path:
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE t (v TEXT)")
    db.commit()
    db.close()
    return path


@pytest.fixture(autouse=True)
async def _a_whole_copy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The copy is the settings' database (the drill's container points it
    there): a whole one, nothing missing, the app's startup stood in."""

    async def missing(session: object) -> list[str]:
        return []

    async def started() -> dict[str, object]:
        return STARTED

    monkeypatch.setattr(drill, "missing_objects", missing)
    monkeypatch.setattr(drill, "_started", started)
    monkeypatch.setattr(drill, "DATABASE_PATH", str(_database(tmp_path / "app.db")))
    await get_cache().clear()


def _by_name(checks: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(c["name"]): c for c in checks}


async def test_a_whole_copy_passes() -> None:
    checks = _by_name(await drill.drill_check())
    assert all(c["ok"] for c in checks.values()), checks
    assert list(checks) == ["database", "startup", "secrets", "files"]


async def test_a_damaged_database_is_checked_no_further(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read only, before the startup writes to it, which would only fail
    on it less clearly."""
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"SQLite format 3\x00" + b"\xff" * 4096)
    monkeypatch.setattr(drill, "DATABASE_PATH", str(broken))
    (check,) = await drill.drill_check()
    assert (check["name"], check["ok"]) == ("database", False)


async def test_a_copy_the_app_cannot_start_on_is_checked_no_further(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refused = {"name": "startup", "ok": False, "said": "no such table: x"}

    async def started() -> dict[str, object]:
        return refused

    monkeypatch.setattr(drill, "_started", started)
    assert [c["name"] for c in await drill.drill_check()] == ["database", "startup"]


async def test_the_startup_is_the_apps_own_and_says_why_it_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """As a restored server boots: what the startup raises, its last line,
    is what the drill says."""
    from app.components.backend.startup import database_init

    async def refuses() -> None:
        raise RuntimeError("Migration 061 failed\nno such table: deploy")

    monkeypatch.setattr(database_init, "startup_database_init", refuses)
    assert await real_started() == {
        "name": "startup",
        "ok": False,
        "said": "no such table: deploy",
    }


async def test_a_secret_this_key_cannot_read_fails_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The copy is only as good as the key kept off the server: restored
    with the wrong one, the drill says so."""
    secrets_store = pytest.importorskip("app.components.secrets.store").DatabaseStore()
    from app.core import encryption  # the secrets component's

    await secrets_store.put("RESEND_API_KEY", "re_live_value", "alue", "test")
    try:
        monkeypatch.setitem(
            settings.__dict__, "ENCRYPTION_KEY", "a different key entirely"
        )
        encryption._reset_cache()
        await get_cache().clear()
        checks = _by_name(await drill.drill_check())
        assert checks["secrets"]["ok"] is False
        assert "RESEND_API_KEY" in str(checks["secrets"]["said"])
    finally:
        await secrets_store.delete("RESEND_API_KEY", "test")
        encryption._reset_cache()


async def test_a_stored_file_missing_from_the_copy_fails_by_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The count, never the keys: the check lands in a deploy event, whose
    JSON ``missing_objects`` reads, and would name them missing forever."""

    async def missing(session: object) -> list[str]:
        return [content_key(b"gone")]

    monkeypatch.setattr(drill, "missing_objects", missing)
    files = _by_name(await drill.drill_check())["files"]
    assert files == {"name": "files", "ok": False, "said": "1 stored file missing"}


def test_the_cli_says_each_check_on_its_last_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``aegis deploy-restore-drill`` reads that line and judges it; the
    exit says only that the checks ran."""

    async def checks() -> list[dict[str, object]]:
        return [
            {"name": "database", "ok": True, "said": "integrity: ok"},
            {"name": "files", "ok": False, "said": "1 stored file missing"},
        ]

    monkeypatch.setattr(drill, "drill_check", checks)
    result = invoke(["deploy", "drill-check"])
    assert result.exit_code == 0
    said = json.loads(result.output.strip().splitlines()[-1])
    assert [c["name"] for c in said] == ["database", "files"]
