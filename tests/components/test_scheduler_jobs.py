"""Where a scheduled job runs.

With a worker, the scheduler only produces: every service job is scheduled
as an enqueue of its own name onto the system queue, and a worker runs it
under the worker's sizing, with its retries and its live feed. The
heartbeat stays in the scheduler, since it proves the scheduler's own loop
is alive. Without a worker, the scheduler runs each job itself.
"""

import logging
from typing import Any

from apscheduler.job import Job
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytest

from app.components.scheduler.main import _quiet_successful_runs, register_service_jobs
from app.core.schedule import ServiceJob, service_jobs


def _scheduled() -> dict[str, Job]:
    scheduler = AsyncIOScheduler()
    register_service_jobs(scheduler)
    return {job.id: job for job in scheduler.get_jobs()}


def test_every_service_job_is_scheduled() -> None:
    assert set(_scheduled()) == {job.id for job in service_jobs()}


def test_a_run_that_succeeds_logs_nothing() -> None:
    """APScheduler logs every run at INFO; the heartbeat alone wrote two
    lines every 15 seconds. Failures still log."""
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.INFO)
    try:
        _quiet_successful_runs()
        executor = logging.getLogger("apscheduler.executors.default")
        assert not executor.isEnabledFor(logging.INFO)
        assert executor.isEnabledFor(logging.ERROR)
    finally:
        root.setLevel(previous)


def test_the_scheduler_only_enqueues() -> None:
    from app.components.worker.pools import enqueue_task

    scheduled = _scheduled()
    for job in service_jobs():
        assert scheduled[job.id].func is enqueue_task, job.id
        assert list(scheduled[job.id].args) == [job.task_name, "system"]


def test_every_enqueued_name_is_a_system_task() -> None:
    """Pins the scheduler's list to the worker's: a name the worker does not
    register would be enqueued forever and run never."""
    from app.components.worker.registry import queue_tasks

    missing = {job.task_name for job in service_jobs()} - set(queue_tasks("system"))
    assert not missing, f"scheduled but not registered on the worker: {missing}"


async def _fake_job() -> None:
    """A job that records that it ran."""
    from structlog.contextvars import get_contextvars

    _RAN.append(get_contextvars().get("app_service"))


_RAN: list[str | None] = []
_FAKE = ServiceJob(
    _fake_job,
    "fake",
    "Fake",
    {"trigger": "interval", "hours": 1},
    timeout=123,
    app_service="demo",
)


async def test_a_worker_task_runs_its_job_under_its_own_name() -> None:
    from app.components.worker.tasks.service_jobs import as_task

    _RAN.clear()
    task = as_task(_FAKE)

    assert task.name == "_fake_job"
    assert task.timeout_s == 123
    await task.coroutine({})
    assert _RAN == ["demo"]


async def test_running_a_job_by_hand_passes_its_stored_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scheduled enqueue is stored with the task name as its argument;
    "Run Now" calling it bare would fail every time."""
    from app.services.scheduler import trigger

    monkeypatch.setattr(trigger, "record_job_started", lambda *_: 1)
    monkeypatch.setattr(trigger, "record_job_finished", lambda *_, **__: None)
    seen: list[tuple[Any, ...]] = []

    async def job(*args: Any) -> None:
        seen.append(args)

    assert await trigger.run_triggered_job(job, "id", "Name", ["a_job", "system"])
    assert seen == [("a_job", "system")]


def test_interval_jobs_keep_their_times_across_restarts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every startup re-adds each job. An interval trigger with no start
    date counts from that moment, so each restart pushed every interval job
    back a full interval (in dev, a daily job restarted hourly never ran).
    Anchored to a fixed start, the next run is the same whenever it starts."""
    from datetime import UTC, datetime, timedelta

    import app.components.scheduler.main as scheduler_main
    from app.core.schedule import ServiceJob

    async def daily() -> None:
        """A daily chore."""

    job = ServiceJob(daily, "daily", "Daily", {"trigger": "interval", "hours": 24})
    monkeypatch.setattr(scheduler_main, "service_jobs", lambda: (job,))
    trigger = _scheduled()["daily"].trigger
    started = [
        datetime(2026, 9, 26, 22, 0, tzinfo=UTC),
        datetime(2026, 9, 26, 23, 54, tzinfo=UTC),
    ]
    next_runs = {trigger.get_next_fire_time(None, now) for now in started}
    assert len(next_runs) == 1
    (next_run,) = next_runs
    assert next_run - min(started) < timedelta(hours=24, seconds=1)
