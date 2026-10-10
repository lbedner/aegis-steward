"""Scheduled service jobs, run as worker tasks on the system queue.

The scheduler enqueues each job by its function's name; this is where the
worker learns those names. Both read ``app.core.schedule.service_jobs``,
so the two cannot drift apart.
"""

import functools
from typing import Any

from arq.worker import Function, func

from app.core.schedule import ServiceJob, service_jobs


def as_task(job: ServiceJob) -> Function:
    """One job as an arq task under the job's own name and timeout."""

    @functools.wraps(job.func)
    async def run(ctx: dict[str, Any]) -> Any:
        return await job.run()

    return func(run, name=job.task_name, timeout=job.timeout)


def service_job_tasks() -> list[Function]:
    """Every scheduled job, for ``WorkerSettings.functions``."""
    return [as_task(job) for job in service_jobs()]
