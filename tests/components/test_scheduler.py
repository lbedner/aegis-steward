"""
Tests for scheduler functionality.

Note: The scheduler focuses entirely on system service monitoring.
We test the service functions directly rather than complex scheduler components.

For integration tests of the actual scheduler, see the CLI tests that generate
complete projects and validate they work correctly.
"""

import asyncio
from typing import Any

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytest
from sqlalchemy.exc import OperationalError

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


class _LockedOnce(MemoryJobStore):
    """A jobstore whose first write back fails the way SQLite did."""

    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def update_job(self, job: Any) -> None:
        if not self.failed:
            self.failed = True
            raise OperationalError(
                "UPDATE apscheduler_jobs", {}, Exception("database is locked")
            )
        super().update_job(job)


async def _runs_after_a_locked_write(scheduler_class: type[AsyncIOScheduler]) -> int:
    fired: list[float] = []
    scheduler = scheduler_class(
        jobstores={"default": _LockedOnce()}, jobstore_retry_interval=0.05
    )
    scheduler.add_job(lambda: fired.append(1), "interval", seconds=0.05)
    scheduler.start()
    await asyncio.sleep(0.6)
    scheduler.shutdown(wait=False)
    return len(fired)


@pytest.mark.asyncio
async def test_a_locked_write_back_does_not_stop_the_scheduler() -> None:
    """2026-09-25 08:43: after host sleep every missed job came due, the
    write back of one next_run_time hit "database is locked", and the
    error escaped APScheduler's wakeup before it re-armed its timer. The
    process stayed up and ran nothing for six hours."""
    from app.components.scheduler.wakeup import StewardScheduler

    assert await _runs_after_a_locked_write(AsyncIOScheduler) == 1  # the stall
    assert await _runs_after_a_locked_write(StewardScheduler) > 2


class TestTheSchedulerOnlyProduces:
    """The scheduler decides WHEN; a worker does the work. Running jobs in
    the scheduler process is what piled every missed job into one process
    after host sleep (2026-09-25). The tasks are ordinary worker tasks:
    the scheduler is one producer among several."""

    def test_every_job_but_the_heartbeat_is_handed_to_a_worker(self) -> None:
        from app.components.scheduler.handoff import enqueue_task
        from app.components.scheduler.heartbeat import HEARTBEAT_JOB_ID
        from app.components.scheduler.main import create_scheduler

        jobs = create_scheduler().get_jobs()
        assert len(jobs) > 5
        for job in jobs:
            if job.id == HEARTBEAT_JOB_ID:
                assert job.func is not enqueue_task  # proves THIS loop is alive
            else:
                assert job.func is enqueue_task, job.id

    def test_every_handed_off_name_is_a_system_task(self) -> None:
        from app.components.scheduler.handoff import enqueue_task
        from app.components.scheduler.main import create_scheduler
        from app.components.worker.queues.system import WorkerSettings
        from app.components.worker.registry import task_name

        registered = {task_name(f) for f in WorkerSettings.functions}
        for job in create_scheduler().get_jobs():
            if job.func is enqueue_task:
                assert job.args[0] in registered, job.args[0]

    @pytest.mark.asyncio
    async def test_enqueue_puts_the_task_on_the_system_queue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import AsyncMock

        from app.components.scheduler import handoff

        pool = AsyncMock()

        async def _pool(queue: str) -> tuple[Any, str]:
            assert queue == "system"
            return pool, "arq:queue:system"

        monkeypatch.setattr(handoff, "get_queue_pool", _pool)
        await handoff.enqueue_task("backup_database_job")
        pool.enqueue_job.assert_awaited_once_with(
            "backup_database_job", _queue_name="arq:queue:system"
        )

    @pytest.mark.asyncio
    async def test_a_task_runs_its_job(self) -> None:
        from app.components.worker.tasks.service_jobs import as_task

        ran: list[str] = []

        async def nightly_job() -> dict[str, str]:
            """Does the nightly thing."""
            ran.append("yes")
            return {"status": "ok"}

        task = as_task(nightly_job, timeout=60)
        assert task.name == "nightly_job"
        assert task.timeout_s == 60
        assert await task.coroutine({}) == {"status": "ok"}
        assert ran == ["yes"]


@pytest.mark.asyncio
async def test_running_a_job_by_hand_hands_it_to_the_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Overseer's run button and ``tasks trigger`` run the stored job
    with its stored arguments - for a handed-off job, an enqueue."""
    from unittest.mock import AsyncMock

    from app.components.scheduler import handoff
    from app.services.scheduler import trigger

    pool = AsyncMock()

    async def _pool(queue: str) -> tuple[Any, str]:
        return pool, "arq:queue:system"

    monkeypatch.setattr(handoff, "get_queue_pool", _pool)
    monkeypatch.setattr(trigger, "record_job_started", lambda *a, **k: 1)
    monkeypatch.setattr(trigger, "record_job_finished", lambda *a, **k: None)

    ok = await trigger.run_triggered_job(
        handoff.enqueue_task,
        "database_backup",
        "Daily Database Backup",
        ["backup_database_job"],
    )

    assert ok
    pool.enqueue_job.assert_awaited_once_with(
        "backup_database_job", _queue_name="arq:queue:system"
    )
