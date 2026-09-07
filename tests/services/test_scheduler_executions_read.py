"""Tests for scheduler execution read queries (ScheduledTaskManager)."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.scheduler.models import JobExecution
import app.services.scheduler.scheduled_task_manager as stm
from app.services.scheduler.scheduled_task_manager import ScheduledTaskManager

_BASE = datetime(2026, 1, 1)


@pytest.fixture
def manager(
    async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> ScheduledTaskManager:
    """Route the manager's ``get_async_session`` to the test session."""

    @asynccontextmanager
    async def _session():
        yield async_db_session

    monkeypatch.setattr(stm, "get_async_session", _session)
    return ScheduledTaskManager()


async def _seed(session: AsyncSession, rows: list[dict]) -> None:
    for row in rows:
        session.add(JobExecution(**row))
    await session.flush()


async def test_list_executions_paginates_newest_first(
    async_db_session: AsyncSession, manager: ScheduledTaskManager
) -> None:
    await _seed(
        async_db_session,
        [
            {
                "job_id": "j1",
                "job_name": "J1",
                "started_at": _BASE + timedelta(minutes=i),
                "status": "success",
            }
            for i in range(5)
        ],
    )

    records, total = await manager.list_executions(offset=0, limit=2)

    assert total == 5
    assert len(records) == 2
    assert records[0]["started_at"] > records[1]["started_at"]  # newest first
    assert "traceback" not in records[0]  # omitted from list payloads


async def test_list_executions_status_and_job_filters(
    async_db_session: AsyncSession, manager: ScheduledTaskManager
) -> None:
    await _seed(
        async_db_session,
        [
            {"job_id": "j1", "started_at": _BASE, "status": "success"},
            {"job_id": "j1", "started_at": _BASE, "status": "failed"},
            {"job_id": "j2", "started_at": _BASE, "status": "failed"},
        ],
    )

    _, total_failed = await manager.list_executions(status="failed")
    assert total_failed == 2

    _, total_j1 = await manager.list_executions(job_id="j1")
    assert total_j1 == 2

    records, total_j1_failed = await manager.list_executions(
        status="failed", job_id="j1"
    )
    assert total_j1_failed == 1
    assert records[0]["status"] == "failed"
    assert records[0]["job_id"] == "j1"


async def test_get_job_stats_computes_rate_and_avg(
    async_db_session: AsyncSession, manager: ScheduledTaskManager
) -> None:
    await _seed(
        async_db_session,
        [
            {
                "job_id": "j1",
                "job_name": "J1",
                "started_at": _BASE,
                "status": "success",
                "duration_ms": 100.0,
            },
            {
                "job_id": "j1",
                "job_name": "J1",
                "started_at": _BASE + timedelta(minutes=1),
                "status": "success",
                "duration_ms": 200.0,
            },
            {
                "job_id": "j1",
                "job_name": "J1",
                "started_at": _BASE + timedelta(minutes=2),
                "status": "failed",
                "duration_ms": 300.0,
            },
        ],
    )

    stats = await manager.get_job_stats("j1")

    assert stats["total_runs"] == 3
    assert stats["success_count"] == 2
    assert stats["failure_count"] == 1
    assert stats["success_rate"] == round(2 / 3 * 100, 1)
    assert stats["avg_duration_ms"] == 200.0
    assert stats["last_run"]["status"] == "failed"  # newest run


async def test_get_job_stats_empty(manager: ScheduledTaskManager) -> None:
    stats = await manager.get_job_stats("does-not-exist")

    assert stats["total_runs"] == 0
    assert stats["success_count"] == 0
    assert stats["success_rate"] == 0.0
    assert stats["avg_duration_ms"] is None
    assert stats["last_run"] is None


async def test_is_job_running_true_when_latest_running(
    async_db_session: AsyncSession, manager: ScheduledTaskManager
) -> None:
    await _seed(
        async_db_session,
        [{"job_id": "j1", "started_at": _BASE, "status": "running"}],
    )
    assert await manager.is_job_running("j1") is True
    assert await manager.is_job_running("other") is False


async def test_is_job_running_false_when_latest_finished(
    async_db_session: AsyncSession, manager: ScheduledTaskManager
) -> None:
    await _seed(
        async_db_session,
        [
            {"job_id": "j1", "started_at": _BASE, "status": "running"},
            {
                "job_id": "j1",
                "started_at": _BASE + timedelta(minutes=1),
                "status": "success",
            },
        ],
    )
    assert await manager.is_job_running("j1") is False
