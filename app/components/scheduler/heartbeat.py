"""Scheduler liveness heartbeat.

An interval job touches a beacon file; the container healthcheck runs this
module (``python -m app.components.scheduler.heartbeat``), which exits
non-zero once the beacon is older than the staleness window.
"""

from pathlib import Path
import sys
import time
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

HEARTBEAT_FILE = Path("/tmp/aegis-scheduler-heartbeat")
HEARTBEAT_JOB_ID = "scheduler_heartbeat"
MAX_AGE_SECONDS = 60


async def touch_scheduler_heartbeat() -> None:
    """Liveness beacon proving the scheduler is actually firing jobs."""
    HEARTBEAT_FILE.touch()


def register_heartbeat_job(scheduler: AsyncIOScheduler) -> None:
    """Register the beacon job on the scheduler."""
    scheduler.add_job(
        touch_scheduler_heartbeat,
        trigger="interval",
        seconds=15,
        id=HEARTBEAT_JOB_ID,
        name="Scheduler Heartbeat",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )


def is_heartbeat_event(event: Any) -> bool:
    """True for job events the execution log should ignore: the heartbeat
    fires constantly and would flood activity and history."""
    return bool(getattr(event, "job_id", None) == HEARTBEAT_JOB_ID)


def is_fresh() -> bool:
    """True while the beacon was touched within the staleness window."""
    try:
        age = time.time() - HEARTBEAT_FILE.stat().st_mtime
    except FileNotFoundError:
        return False
    return age < MAX_AGE_SECONDS


if __name__ == "__main__":
    sys.exit(0 if is_fresh() else 1)
