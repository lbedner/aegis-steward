"""Service jobs as worker tasks, named after the function they run.

The scheduler enqueues these on a timer, but it is one producer among
several: anything may enqueue ``"backup_database_job"``. The work runs here,
on a worker, not in whichever process asked for it.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from arq.worker import Function, func

from app.services.ai.jobs import analyze_sentiment_job, sync_llm_catalog_job
from app.services.documents.domains.reading.joins import join_arrivals_job
from app.services.finance.jobs import (
    finance_bill_due_email_job,
    finance_envelope_credit_job,
    finance_goal_auto_contribute_job,
    finance_recompute_snapshots_job,
    finance_sync_connections_job,
)
from app.services.matters.jobs import matters_deadline_nag_job
from app.services.system.backup import backup_database_job

# ponytail: a guess. The recorded run times are wall clock spanning host
# sleep (maxima of hours), so they cannot size this; tighten from the
# worker's own task history once it has a few weeks of real runs.
LONG_RUNNING_SECONDS = 3600


def as_task(
    job: Callable[[], Awaitable[Any]], timeout: float | None = None
) -> Function:
    """An arq task that runs ``job`` and is enqueued by ``job``'s name."""

    async def run(ctx: dict[str, Any]) -> Any:
        return await job()

    run.__doc__ = job.__doc__
    return func(run, name=job.__name__, timeout=timeout)


SERVICE_JOB_TASKS = [
    as_task(backup_database_job, timeout=LONG_RUNNING_SECONDS),
    as_task(finance_recompute_snapshots_job, timeout=LONG_RUNNING_SECONDS),
    as_task(finance_sync_connections_job, timeout=LONG_RUNNING_SECONDS),
    as_task(sync_llm_catalog_job, timeout=LONG_RUNNING_SECONDS),
    as_task(matters_deadline_nag_job, timeout=LONG_RUNNING_SECONDS),
    as_task(join_arrivals_job),
    as_task(analyze_sentiment_job),
    as_task(finance_goal_auto_contribute_job),
    as_task(finance_envelope_credit_job),
    as_task(finance_bill_due_email_job),
]
