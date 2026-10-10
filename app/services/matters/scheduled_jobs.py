"""The matters service's scheduled jobs (``app.core.schedule``)."""

from app.core.schedule import LONG_RUNNING, ServiceJob
from app.services.matters.jobs import matters_deadline_nag_job

JOBS: tuple[ServiceJob, ...] = (
    # Early, before the day's work: a deadline you hear about in the
    # morning is one you can still do something about, and the sidebar's
    # dot only ever appeared once the day had passed.
    ServiceJob(
        matters_deadline_nag_job,
        "matters_deadline_nag",
        "Matters: Nag Approaching Deadlines",
        {"trigger": "cron", "hour": 6, "minute": 30},
        timeout=LONG_RUNNING,
    ),
)
