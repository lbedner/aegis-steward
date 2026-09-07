"""
Tests for worker event streaming infrastructure.

Tests publish_event, EventPublishMiddleware (TaskIQ only), and
pure helper functions _format_eta and _compute_queue_values.
"""

from unittest.mock import AsyncMock

import pytest

from app.components.worker.events import WORKER_EVENT_STREAM, publish_event

# ---------------------------------------------------------------------------
# Group 1: publish_event()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_event_calls_xadd() -> None:
    """publish_event should call xadd on the Redis Stream with correct fields."""
    redis = AsyncMock()
    await publish_event(redis, "job.started", "system")

    redis.xadd.assert_called_once()
    call_args = redis.xadd.call_args
    assert call_args[0][0] == WORKER_EVENT_STREAM
    fields = call_args[0][1]
    assert fields["type"] == "job.started"
    assert fields["queue"] == "system"
    assert "timestamp" in fields


@pytest.mark.asyncio
async def test_publish_event_includes_metadata() -> None:
    """Extra metadata should be merged into the event fields."""
    redis = AsyncMock()
    await publish_event(
        redis, "job.completed", "load_test", {"job_id": "abc123", "task": "cpu"}
    )

    fields = redis.xadd.call_args[0][1]
    assert fields["job_id"] == "abc123"
    assert fields["task"] == "cpu"
    assert fields["type"] == "job.completed"


@pytest.mark.asyncio
async def test_publish_event_swallows_redis_errors() -> None:
    """publish_event should never propagate exceptions from Redis."""
    redis = AsyncMock()
    redis.xadd.side_effect = ConnectionError("Redis down")

    # Should NOT raise
    await publish_event(redis, "job.started", "system")


# ---------------------------------------------------------------------------
# Group 3: _format_eta()
# ---------------------------------------------------------------------------

from app.components.frontend.dashboard.modals.worker_modal import (  # noqa: E402
    _format_eta,
)


def test_format_eta_subsecond() -> None:
    """Sub-second values should display as dash."""
    assert _format_eta(0.5) == "—"


def test_format_eta_seconds() -> None:
    """Values under 60s should display as whole seconds."""
    assert _format_eta(42) == "42s"


def test_format_eta_minutes() -> None:
    """Values under 1h should display as minutes and seconds."""
    assert _format_eta(195) == "3m 15s"


def test_format_eta_hours() -> None:
    """Values over 1h should display as hours and minutes."""
    assert _format_eta(9000) == "2h 30m"


# ---------------------------------------------------------------------------
# Group 4: _compute_queue_values()
# ---------------------------------------------------------------------------

from app.components.frontend.dashboard.modals.worker_modal import (  # noqa: E402
    _compute_queue_values,
)
from app.components.frontend.theme import AegisTheme as Theme  # noqa: E402
from app.services.system.models import ComponentStatus  # noqa: E402


def _make_queue_status(
    *,
    worker_alive: bool = True,
    queued_jobs: int = 0,
    jobs_ongoing: int = 0,
    jobs_completed: int = 0,
    jobs_failed: int = 0,
    failure_rate_percent: float = 0.0,
    message: str = "",
) -> ComponentStatus:
    """Build a ComponentStatus for a queue with the given metadata."""
    return ComponentStatus(
        name="test_queue",
        status="healthy",
        message=message,
        metadata={
            "worker_alive": worker_alive,
            "queued_jobs": queued_jobs,
            "jobs_ongoing": jobs_ongoing,
            "jobs_completed": jobs_completed,
            "jobs_failed": jobs_failed,
            "failure_rate_percent": failure_rate_percent,
        },
    )


def test_queue_values_online() -> None:
    """Alive worker with no active jobs should show Online / green."""
    vals = _compute_queue_values(_make_queue_status(worker_alive=True))
    assert vals["status_text"] == "Online"
    assert vals["status_color"] == Theme.Colors.SUCCESS


def test_queue_values_active() -> None:
    """Alive worker with ongoing jobs should show Active / blue."""
    vals = _compute_queue_values(_make_queue_status(worker_alive=True, jobs_ongoing=3))
    assert vals["status_text"] == "Active"
    assert vals["status_color"] == Theme.Colors.INFO


def test_queue_values_offline() -> None:
    """Dead worker should show Offline / red."""
    vals = _compute_queue_values(_make_queue_status(worker_alive=False))
    assert vals["status_text"] == "Offline"
    assert vals["status_color"] == Theme.Colors.ERROR


def test_queue_values_degraded() -> None:
    """Failure rate above warning threshold should show Degraded."""
    vals = _compute_queue_values(
        _make_queue_status(
            worker_alive=True,
            jobs_completed=90,
            jobs_failed=10,
            failure_rate_percent=10.0,
        )
    )
    assert vals["status_text"] == "Degraded"


def test_queue_values_failing() -> None:
    """Failure rate above critical threshold should show Failing."""
    vals = _compute_queue_values(
        _make_queue_status(
            worker_alive=True,
            jobs_completed=75,
            jobs_failed=25,
            failure_rate_percent=25.0,
        )
    )
    assert vals["status_text"] == "Failing"
