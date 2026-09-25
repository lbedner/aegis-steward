"""A scheduler that re-arms its timer even when a pass over the jobs fails.

APScheduler 3 retries a failed READ of due jobs, but writing a job's next
run time back (``jobstore.update_job``) is unguarded: an error there escapes
``wakeup()`` before it starts the next timer, and the scheduler never wakes
again while its process stays up. 2026-09-25 08:43: after host sleep every
missed job came due at once, one write back hit "database is locked", and
nothing ran for six hours; restart policies never fire for a live process.

A failed pass is logged and retried after ``jobstore_retry_interval``. The
job whose write back failed runs again on that retry - the scheduler is
already configured on the assumption that jobs are idempotent.
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.log import logger


class StewardScheduler(AsyncIOScheduler):
    def wakeup(self) -> None:
        self._stop_timer()
        try:
            wait_seconds = self._process_jobs()
        except Exception:
            logger.exception("Scheduler pass failed; retrying")
            wait_seconds = self.jobstore_retry_interval
        self._start_timer(wait_seconds)
