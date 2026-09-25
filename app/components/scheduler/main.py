"""
Scheduler component for aegis-steward.

Simple, explicit job scheduling - just import functions and schedule them.
Add your own jobs by importing service functions and calling scheduler.add_job().
"""

import asyncio
import logging
from typing import Any

from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MISSED,
    EVENT_JOB_SUBMITTED,
)
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import settings
from app.core.db import db_session, engine, init_database
from app.core.log import logger
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
from app.services.scheduler.execution_log import (
    cancel_stale_running_rows,
    prune_executions,
    record_job_finished,
    record_job_missed,
    record_job_started,
)
from app.services.scheduler.orphans import drop_unknown_persisted_jobs
from app.services.system import activity
from app.services.system.backup import backup_database_job

from .handoff import enqueue_task
from .heartbeat import is_heartbeat_event, register_heartbeat_job
from .wakeup import StewardScheduler


def _cleanup_stale_jobs() -> None:
    """Remove persisted jobs whose func_ref can no longer be imported.

    This handles Docker volumes persisting jobs from a previous project
    configuration (e.g., AI service was enabled before but isn't now).
    """
    import pickle

    from sqlmodel import text

    try:
        with db_session() as session:
            rows = session.exec(
                text("SELECT id, job_state FROM apscheduler_jobs")
            ).fetchall()

        stale_ids: list[str] = []
        for job_id, job_state_bytes in rows:
            try:
                state = pickle.loads(job_state_bytes)
                func_ref = state.get("func", "")
                if ":" in func_ref:
                    module_name = func_ref.split(":")[0]
                    __import__(module_name)
            except (ImportError, ModuleNotFoundError):
                stale_ids.append(job_id)
            except Exception:
                pass

        if stale_ids:
            with db_session(autocommit=True) as session:
                for job_id in stale_ids:
                    session.exec(
                        text("DELETE FROM apscheduler_jobs WHERE id = :id"),
                        params={"id": job_id},
                    )
            logger.warning(
                f"Removed {len(stale_ids)} stale scheduled job(s) "
                f"from persistent store: {stale_ids}"
            )
    except Exception as e:
        logger.debug(f"Stale job cleanup skipped: {e}")


async def _apply_active_model() -> None:
    """Replay the stored active-model selection into this process's settings.

    Mirrors the backend's ``llm_active_model`` startup hook. A missing table
    (migrations not yet run) must not stop the scheduler from booting: without
    an override the .env default is already correct.
    """
    from app.core.config import settings
    from app.core.db import get_async_session
    from app.services.ai.domains.llm import active_model

    try:
        async with get_async_session() as session:
            applied = await active_model.load_into_settings(session, settings)
    except Exception:
        logger.exception("Could not load the active LLM selection")
        return
    if applied:
        logger.info(
            "Active LLM selection applied: %s (%s)",
            settings.AI_MODEL,
            settings.AI_PROVIDER,
        )


def create_scheduler() -> AsyncIOScheduler:
    """Create and configure the scheduler with all jobs."""

    # APScheduler logs every run at INFO: two lines per heartbeat every 15s,
    # and every other run is an enqueue the worker logs for real. Failures
    # still log at ERROR.
    logging.getLogger("apscheduler.executors").setLevel(logging.WARNING)

    # Ensure database is initialized before creating jobstore
    init_database()

    # Clean up stale jobs from persistent store before loading
    # (handles Docker volume persisting jobs from a previous project config)
    _cleanup_stale_jobs()

    # Configure SQLAlchemy jobstore for persistence
    jobstore = SQLAlchemyJobStore(engine=engine, tablename="apscheduler_jobs")
    jobstores = {"default": jobstore}
    # misfire_grace_time=None: always run missed jobs when the scheduler wakes
    # (host sleep, container pause, deploy gap). Assumes jobs are idempotent.
    # coalesce=True collapses multiple missed runs into a single catch-up.
    job_defaults = {"misfire_grace_time": None, "coalesce": True}
    scheduler = StewardScheduler(
        jobstores=jobstores,
        job_defaults=job_defaults,
        timezone=settings.SCHEDULER_TIMEZONE,
    )
    logger.info("Scheduler using sqlite database for job persistence")

    # JOB SCHEDULE CONFIGURATION - code is the source of truth. Every startup
    # re-registers each job below via ``replace_existing=True``, so editing a
    # trigger here and redeploying changes the schedule; runtime edits to
    # persisted jobs do not survive a restart by design.
    register_heartbeat_job(scheduler)

    scheduler.add_job(
        enqueue_task,
        args=[backup_database_job.__name__],
        trigger="cron",
        hour=2,
        minute=0,
        id="database_backup",
        name="Daily Database Backup",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # After the day's post has been read: what arrived is joined to the
    # asks it answers, as cards. Late in the evening because a document
    # read at 6pm should be joined the same night, and idempotent, so a
    # missed run simply catches up.
    scheduler.add_job(
        enqueue_task,
        args=[join_arrivals_job.__name__],
        trigger="cron",
        hour=23,
        minute=0,
        id="join_arrivals",
        name="Join The Day's Arrivals",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    scheduler.add_job(
        enqueue_task,
        args=[sync_llm_catalog_job.__name__],
        trigger="interval",
        hours=6,
        id="llm_sync",
        name="LLM Catalog Sync",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    scheduler.add_job(
        enqueue_task,
        args=[analyze_sentiment_job.__name__],
        trigger="interval",
        hours=1,
        id="sentiment_analysis",
        name="Conversation Sentiment Analysis",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # Net-worth engine: materialize per-account balance + per-user net-worth
    # snapshots nightly so the net-worth-over-time chart is a cheap range scan.
    scheduler.add_job(
        enqueue_task,
        args=[finance_recompute_snapshots_job.__name__],
        trigger="cron",
        hour=2,
        id="finance_recompute_snapshots",
        name="Finance: Recompute Net-Worth Snapshots",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # Goals: book toggled-on virtual goals' declared amounts on the 1st.
    # Idempotent per month, so a missed run caught up later books nothing
    # twice - the plan saves unless actively paused.
    scheduler.add_job(
        enqueue_task,
        args=[finance_goal_auto_contribute_job.__name__],
        trigger="cron",
        day=1,
        hour=2,
        minute=45,
        id="finance_goal_auto_contribute",
        name="Finance: Auto-Contribute to Goals",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # Envelopes: the allowance arrives on its cadence (weekly credits
    # land Mondays), so the job checks DAILY and the per-period
    # idempotency inside it decides whether anything books.
    scheduler.add_job(
        enqueue_task,
        args=[finance_envelope_credit_job.__name__],
        trigger="cron",
        hour=2,
        minute=50,
        id="finance_envelope_credit",
        name="Finance: Credit Envelopes",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # Keep linked banks fresh: pull new transactions/balances a few times a day
    # so the register isn't stale between logins (webhooks handle the real-time
    # nudge when a public URL is configured).
    scheduler.add_job(
        enqueue_task,
        args=[finance_sync_connections_job.__name__],
        trigger="interval",
        hours=6,
        id="finance_sync_connections",
        name="Finance: Sync Bank Connections",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # Morning, after the overnight passes: what is due in the next few
    # days, mailed once. Does nothing at all until FINANCE_BILL_EMAIL_TO
    # is set, so an unconfigured project mails nobody.
    scheduler.add_job(
        enqueue_task,
        args=[finance_bill_due_email_job.__name__],
        trigger="cron",
        hour=7,
        minute=0,
        id="finance_bill_due_email",
        name="Finance: Email Bills Coming Due",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # Early, before the day's work: a deadline you hear about in the
    # morning is one you can still do something about, and the sidebar's
    # dot only ever appeared once the day had passed.
    scheduler.add_job(
        enqueue_task,
        args=[matters_deadline_nag_job.__name__],
        trigger="cron",
        hour=6,
        minute=30,
        id="matters_deadline_nag",
        name="Matters: Nag Approaching Deadlines",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # Add your own scheduled jobs here by importing service functions
    # and calling scheduler.add_job() with your custom business logic

    # Drop persisted jobs whose ``add_job`` call has been removed from
    # code since the last deploy. ``replace_existing=True`` covers the
    # "schedule changed" case for jobs that still exist; this covers the
    # "job deleted" case so persistent storage doesn't keep firing an
    # obsolete job ID forever.
    drop_unknown_persisted_jobs(scheduler)

    return scheduler


async def run_scheduler() -> None:
    """Main scheduler runner with lifecycle management."""

    logger.info("Starting aegis-steward Scheduler")

    # The active model is a database row, and startup hooks are a backend
    # concern this process never runs. Without replaying the selection here,
    # the scheduler would keep answering on whatever .env said while the
    # webserver used the model you actually picked - so a nightly AI job and
    # the dashboard would quietly disagree about which model is in use.
    await _apply_active_model()

    scheduler = create_scheduler()

    # Maps an in-flight run (job_id, scheduled_run_time) to its JobExecution
    # row id, so EXECUTED/ERROR can close the row that SUBMITTED opened. The
    # scheduler is a single process, so a plain dict needs no locking.
    running_executions: dict[tuple[str, str], int] = {}

    def _on_job_event(event: Any) -> None:
        """Emit activity events (and persist execution history) for jobs."""
        if is_heartbeat_event(event):
            return
        # JobSubmissionEvent uses `scheduled_run_times` (list); JobExecutionEvent
        # uses `scheduled_run_time` (singular). Normalise to a single value so
        # the run_key matches across SUBMITTED → EXECUTED/ERROR.
        if event.code == EVENT_JOB_SUBMITTED:
            run_times = getattr(event, "scheduled_run_times", None)
            scheduled = run_times[0] if run_times else None
        else:
            scheduled = getattr(event, "scheduled_run_time", None)
        run_key = (event.job_id, scheduled.isoformat() if scheduled else "")
        if event.code == EVENT_JOB_SUBMITTED:
            job = scheduler.get_job(event.job_id)
            execution_id = record_job_started(
                event.job_id,
                job.name if job else event.job_id,
                scheduled,
            )
            if execution_id is not None:
                running_executions[run_key] = execution_id
            return
        if event.code == EVENT_JOB_EXECUTED:
            activity.add_event(
                component="scheduler",
                event_type="job_complete",
                message=f"Job '{event.job_id}' completed",
                status="success",
            )
            record_job_finished(
                running_executions.pop(run_key, None),
                event.job_id,
                success=True,
            )
            prune_executions(event.job_id)
        elif event.code == EVENT_JOB_ERROR:
            activity.add_event(
                component="scheduler",
                event_type="job_failed",
                message=f"Job '{event.job_id}' failed",
                status="error",
                details=str(event.exception),
            )
            record_job_finished(
                running_executions.pop(run_key, None),
                event.job_id,
                success=False,
                error=str(event.exception),
                traceback=event.traceback,
            )
            prune_executions(event.job_id)
        elif event.code == EVENT_JOB_MISSED:
            activity.add_event(
                component="scheduler",
                event_type="job_missed",
                message=f"Job '{event.job_id}' missed its scheduled run",
                status="warning",
                details=f"scheduled_run={event.scheduled_run_time.isoformat()}",
            )
            record_job_missed(event.job_id, scheduled)

    try:
        scheduler.start()
        scheduler.add_listener(
            _on_job_event,
            EVENT_JOB_EXECUTED
            | EVENT_JOB_ERROR
            | EVENT_JOB_MISSED
            | EVENT_JOB_SUBMITTED,
        )
        cancel_stale_running_rows()
        logger.info("Scheduler started successfully")
        logger.info(f"{len(scheduler.get_jobs())} jobs scheduled:")

        for job in scheduler.get_jobs():
            logger.info(f"   • {job.name} - {job.trigger}")

        # Keep the scheduler running
        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    except Exception as e:
        logger.error(f"Scheduler error: {e}")
        raise
    finally:
        if scheduler.running:
            scheduler.shutdown()
            logger.info("Scheduler stopped gracefully")
