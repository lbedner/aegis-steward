"""Where a mail import runs: the worker when the stack has one, else here.

The finance import's two lanes, for the same reason: an export of ten
thousand messages outlives any request, and a restart landing on it must
not kill it. A worker outlives a reload; the same job id comes back
either way and the jobs API cannot tell the difference.

The bytes travel through storage rather than Redis: the worker mounts
the same volume, and a queue is a poor place to park a file.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.db import get_async_session
from app.core.log import logger
from app.services.system.jobs import JobHandle, SetLabel, get_job_runner


def job_name(file_name: str) -> str:
    """What the job store calls it. ``partials/jobs/status.html`` renders
    a terminal frame per kind of job, so the prefix is load-bearing."""
    return f"mail-import:{file_name}"


async def run_mail_import(
    storage_key: str,
    *,
    file_name: str,
    owner_user_id: int | None,
    on_label: SetLabel | None = None,
) -> dict[str, Any]:
    """The import itself, wherever it is running.

    Hands ``ingest_mail`` a way to OPEN a database rather than an open
    one: the parse and every read dispatch happen holding nothing.
    """
    from app.core.storage import get_storage
    from app.services.mail.ingest import ingest_mail

    data = await get_storage().get(storage_key)
    if data is None:
        raise ValueError("The uploaded file is no longer there.")
    logger.info("mail.import.started", file_name=file_name, bytes=len(data))
    result = await ingest_mail(
        get_async_session,
        data=data,
        file_name=file_name,
        owner_user_id=owner_user_id,
        on_label=on_label,
    )
    return result.as_payload()


def start_mail_import_in_process(
    storage_key: str, *, file_name: str, owner_user_id: int | None
) -> str:
    """Run it as an asyncio task here. Dies with the process, which is
    why it is the fallback rather than the default."""

    async def work(handle: JobHandle) -> dict[str, Any]:
        handle.set_label(f"Reading {file_name}...")
        return await run_mail_import(
            storage_key,
            file_name=file_name,
            owner_user_id=owner_user_id,
            on_label=handle.label_writer(),
        )

    return get_job_runner().start(
        job_name(file_name), work, label=f"Uploading {file_name}..."
    )


async def start_mail_import(
    storage_key: str, *, file_name: str, owner_user_id: int | None
) -> str:
    """Hand it to the worker, or run it here when there is none."""
    try:
        return await _hand_to_worker(
            storage_key, file_name=file_name, owner_user_id=owner_user_id
        )
    except Exception as exc:
        logger.warning("mail.import.no_worker", error=str(exc), file_name=file_name)
        return start_mail_import_in_process(
            storage_key, file_name=file_name, owner_user_id=owner_user_id
        )


async def _hand_to_worker(
    storage_key: str, *, file_name: str, owner_user_id: int | None
) -> str:
    """Record the job in the shared store, then enqueue it."""
    import uuid

    from app.core.config import settings
    from app.services.system.job_store import RedisJobStore

    job_id = uuid.uuid4().hex
    store = RedisJobStore.from_url(settings.REDIS_URL)
    try:
        await store.create(job_id, job_name(file_name), "Queued...")
        try:
            await _enqueue(job_id, storage_key, file_name, owner_user_id)
        except Exception as exc:
            # The record outlives this function: a failed hand-off has to
            # be written into it, or the job sits "Queued..." until it
            # expires with the reason only in these logs.
            await store.fail(job_id, f"Could not queue the import: {exc}")
            raise
    finally:
        await store.aclose()
    logger.info("mail.import.enqueued", job_id=job_id, file_name=file_name)
    return job_id


async def _enqueue(
    job_id: str, storage_key: str, file_name: str, owner_user_id: int | None
) -> None:
    from app.components.worker.pools import get_queue_pool

    pool, queue_name = await get_queue_pool("system")
    await pool.enqueue_job(
        "mail_import_task",
        job_id,
        storage_key,
        file_name,
        owner_user_id,
        _queue_name=queue_name,
    )


async def run_mail_import_job(
    job_id: str, storage_key: str, file_name: str, owner_user_id: int | None
) -> dict[str, Any]:
    """The worker's entry: narrate into the shared job store, then finish."""
    from app.core.config import settings
    from app.services.system.job_store import RedisJobStore

    store = RedisJobStore.from_url(settings.REDIS_URL)
    try:
        await store.set_label(job_id, f"Reading {file_name}...")
        result = await run_mail_import(
            storage_key,
            file_name=file_name,
            owner_user_id=owner_user_id,
            on_label=store.label_writer(job_id),
        )
        await store.finish(job_id, result)
        return result
    except Exception as exc:
        await store.fail(job_id, str(exc) or type(exc).__name__)
        raise
    finally:
        await asyncio.shield(store.aclose())
