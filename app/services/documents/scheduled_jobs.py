"""The documents service's scheduled jobs (``app.core.schedule``)."""

from app.core.schedule import ServiceJob
from app.services.documents.domains.reading.filing import reread_unfiled_job
from app.services.documents.domains.reading.joins import join_arrivals_job

JOBS: tuple[ServiceJob, ...] = (
    # Before the joins: paper that arrived before the household knew what
    # it was about is filed once a fact it prints has landed (#409), so
    # the joins below see it on its account.
    ServiceJob(
        reread_unfiled_job,
        "reread_unfiled",
        "Read The Unfiled Pile Again",
        {"trigger": "cron", "hour": 22, "minute": 30},
    ),
    # After the day's post has been read: what arrived is joined to the
    # asks it answers, as cards. Late in the evening because a document
    # read at 6pm should be joined the same night, and idempotent, so a
    # missed run simply catches up.
    ServiceJob(
        join_arrivals_job,
        "join_arrivals",
        "Join The Day's Arrivals",
        {"trigger": "cron", "hour": 23, "minute": 0},
    ),
)
