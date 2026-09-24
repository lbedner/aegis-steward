"""
Tests for scheduler functionality.

Note: The scheduler focuses entirely on system service monitoring.
We test the service functions directly rather than complex scheduler components.

For integration tests of the actual scheduler, see the CLI tests that generate
complete projects and validate they work correctly.
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytest

from app.services.system.health import check_system_status


@pytest.mark.asyncio
async def test_scheduler_basic_setup() -> None:
    """Test that the scheduler can be set up and jobs can be added."""
    scheduler = AsyncIOScheduler()

    # Add a simple job
    scheduler.add_job(check_system_status, trigger="interval", minutes=5, id="test_job")

    # Check job was added
    jobs = scheduler.get_jobs()
    assert len(jobs) == 1
    assert jobs[0].id == "test_job"


@pytest.mark.asyncio
async def test_system_service_can_be_scheduled() -> None:
    """Test that our system service functions work with APScheduler."""
    scheduler = AsyncIOScheduler()

    # Test that our system service function can be scheduled without errors
    scheduler.add_job(check_system_status, trigger="interval", seconds=1, id="system")

    assert len(scheduler.get_jobs()) == 1

    # Get job function
    system_job = scheduler.get_job("system")

    assert system_job.func == check_system_status


@pytest.mark.asyncio
async def test_scheduler_heartbeat_job_registered() -> None:
    """The heartbeat job exists and its beacon file gets touched."""
    from app.components.scheduler.heartbeat import (
        HEARTBEAT_FILE,
        HEARTBEAT_JOB_ID,
        touch_scheduler_heartbeat,
    )
    from app.components.scheduler.main import create_scheduler

    scheduler = create_scheduler()
    job = scheduler.get_job(HEARTBEAT_JOB_ID)
    assert job is not None
    assert job.trigger.interval.total_seconds() <= 30

    HEARTBEAT_FILE.unlink(missing_ok=True)
    await touch_scheduler_heartbeat()
    assert HEARTBEAT_FILE.exists()


def test_heartbeat_freshness_is_judged_by_the_module(tmp_path, monkeypatch) -> None:
    """The healthcheck asks the module, so the beacon path has one home."""
    import os
    from pathlib import Path

    import yaml

    from app.components.scheduler import heartbeat

    beacon = tmp_path / "beat"
    monkeypatch.setattr(heartbeat, "HEARTBEAT_FILE", beacon)
    assert not heartbeat.is_fresh()
    beacon.touch()
    assert heartbeat.is_fresh()
    stale = beacon.stat().st_mtime - heartbeat.MAX_AGE_SECONDS - 1
    os.utime(beacon, (stale, stale))
    assert not heartbeat.is_fresh()

    compose = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    check = yaml.safe_load(compose.read_text())["services"]["scheduler"]
    assert check["healthcheck"]["test"] == [
        "CMD",
        "python",
        "-m",
        "app.components.scheduler.heartbeat",
    ]


def test_orphan_sweep_exports_before_deleting(tmp_path, monkeypatch) -> None:
    """#1026: the sweep enforces code-as-truth by deleting persisted jobs
    not registered in code. On the first boot after an update that is
    every schedule ever added at runtime (sector-7g lost 26 in one INFO
    line). Deletion must leave a file the operator can restore from."""
    # A memory-backed scheduler ships neither the sweep nor SQLAlchemy;
    # skip before importing either.
    orphans = pytest.importorskip("app.services.scheduler.orphans")
    pytest.importorskip("sqlalchemy")
    from contextlib import contextmanager
    import json
    import pickle

    from sqlalchemy import create_engine, text
    from sqlmodel import Session

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE apscheduler_jobs (id VARCHAR PRIMARY KEY, "
                "next_run_time FLOAT, job_state BLOB)"
            )
        )
        state = pickle.dumps(
            {
                "func": "app.jobs:morning_donut_run",
                "trigger": "cron[hour=7]",
                "kwargs": {"n": 1},
            }
        )
        conn.execute(
            text("INSERT INTO apscheduler_jobs VALUES ('morning_donut_run', 1.0, :s)"),
            {"s": state},
        )
        conn.execute(
            text("INSERT INTO apscheduler_jobs VALUES ('in_code', 2.0, :s)"),
            {"s": state},
        )

    @contextmanager
    def fake_session(autocommit: bool = True):
        with Session(engine) as session:
            yield session
            session.commit()

    monkeypatch.setattr(orphans, "db_session", fake_session)
    monkeypatch.setattr(orphans.settings, "DATABASE_BACKUP_DIR", str(tmp_path))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_system_status, trigger="interval", minutes=5, id="in_code")

    orphans.drop_unknown_persisted_jobs(scheduler)

    with engine.begin() as conn:
        left = {r[0] for r in conn.execute(text("SELECT id FROM apscheduler_jobs"))}
    assert left == {"in_code"}

    exports = list(tmp_path.glob("orphan_jobs_*.json"))
    assert len(exports) == 1, exports
    rows = json.loads(exports[0].read_text())
    assert rows[0]["id"] == "morning_donut_run"
    assert "morning_donut_run" in rows[0]["func"]
    assert rows[0]["kwargs"] == {"n": 1}
