"""Orphan sweep for the persistent jobstore.

Code is the source of truth for scheduled jobs: a persisted row whose id is
not registered in ``create_scheduler`` is deleted on boot, so removing an
``add_job`` call actually stops the job. The cost is that schedules added at
runtime (UI/CLI, persisted only in the jobstore) are orphans by the same
definition - on the first boot after the update that introduced the sweep,
all of them. sector-7g lost 26 that way with one INFO line as the only
trace (aegis-stack#1026). Rows are exported before deletion so the sweep is
recoverable, and nothing is deleted if the export cannot be written.
"""

from datetime import UTC, datetime
import json
from pathlib import Path
import pickle
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import text

from app.core.config import settings
from app.core.db import db_session
from app.core.log import logger


def drop_unknown_persisted_jobs(scheduler: AsyncIOScheduler) -> None:
    """Delete persisted rows whose ID isn't registered in code.

    ``replace_existing=True`` keeps live jobs in sync when their triggers
    change, but does nothing for jobs that were *removed* from
    ``create_scheduler``. Without this sweep, the persistent jobstore
    would keep firing the obsolete job indefinitely — breaking the
    "code is the source of truth" promise. Runs after the ``add_job``
    pass so the set of intended IDs is whatever ``scheduler.get_jobs()``
    reports right now.
    """
    try:
        intended_ids = {job.id for job in scheduler.get_jobs()}
        with db_session() as session:
            persisted_ids = {
                row[0]
                for row in session.exec(
                    text("SELECT id FROM apscheduler_jobs")
                ).fetchall()
            }

        orphans = persisted_ids - intended_ids
        if not orphans:
            return

        # Export before delete. On the first boot after a template update
        # every schedule ever added at runtime (UI/CLI, persisted only in
        # the jobstore) is an "orphan" by this definition, and one INFO
        # line was the only trace of 26 of them going. The file makes the
        # sweep recoverable; nothing is deleted if it cannot be written.
        export_path = _export_orphan_jobs(sorted(orphans))

        with db_session(autocommit=True) as session:
            for job_id in orphans:
                session.exec(
                    text("DELETE FROM apscheduler_jobs WHERE id = :id"),
                    params={"id": job_id},
                )
        logger.warning(
            f"Removed {len(orphans)} orphan scheduled job(s) "
            f"(no longer in code): {sorted(orphans)}. "
            f"Exported to {export_path} - re-add from there if they were wanted."
        )
    except Exception as e:
        logger.debug(f"Orphan job sweep skipped: {e}")


def _export_orphan_jobs(job_ids: list[str]) -> Path:
    """Write the rows the sweep is about to delete to a timestamped JSON file.

    APScheduler stores each job as a pickle in ``job_state``; the fields an
    operator needs to re-add it (func, trigger, args, kwargs) are pulled out
    when it unpickles, and the raw hex kept otherwise so nothing is lost.
    """
    rows: list[dict[str, Any]] = []
    with db_session() as session:
        for job_id in job_ids:
            row = session.exec(
                text(
                    "SELECT next_run_time, job_state FROM apscheduler_jobs WHERE id = :id"
                ),
                params={"id": job_id},
            ).one()
            entry: dict[str, Any] = {"id": job_id, "next_run_time": row[0]}
            try:
                state = pickle.loads(row[1])
                for key in ("func", "trigger", "args", "kwargs"):
                    entry[key] = state.get(key) if isinstance(state, dict) else None
            except Exception:
                entry["job_state_hex"] = bytes(row[1]).hex()
            rows.append(entry)

    export_dir = Path(settings.DATABASE_BACKUP_DIR)
    export_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = export_dir / f"orphan_jobs_{stamp}.json"
    path.write_text(json.dumps(rows, indent=2, default=str))
    return path
