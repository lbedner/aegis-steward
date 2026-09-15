"""Where a file import runs: the worker when the stack has one, else here.

An 18,000-row export takes seconds, which is exactly why it looked safe
to run inside the web process - until a restart landed on one. The job
died with the process, the follower kept spinning, and nothing said so.
A worker outlives a reload; the same job id comes back either way and
the jobs API cannot tell the difference.

The bytes travel through storage rather than Redis: the worker mounts
the same volume, an upload is capped at 10 MB, and a queue is a poor
place to park a file.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.db import get_async_session
from app.core.log import logger
from app.services.system.jobs import JobHandle, get_job_runner


async def run_import(
    storage_key: str,
    *,
    file_name: str,
    account_id: int | None,
    owner_user_id: int | None,
) -> dict[str, Any]:
    """The import itself, in its own session, wherever it is running."""
    from app.components.backend.api.finance.imports import _import_result_payload
    from app.core.storage import get_storage

    data = await get_storage().get(storage_key)
    if data is None:
        raise ValueError("The uploaded file is no longer there.")
    from app.services.finance.service import FinanceService

    async with get_async_session() as session:
        result = await FinanceService(session).import_file(
            owner_user_id=owner_user_id,
            file_name=file_name,
            file_bytes=data,
            account_id=account_id,
        )
        await session.commit()
    return _import_result_payload(result)


def start_import_in_process(
    storage_key: str,
    *,
    file_name: str,
    account_id: int | None,
    owner_user_id: int | None,
) -> str:
    """Run it as an asyncio task here. Dies with the process, which is
    why it is the fallback rather than the default."""

    async def work(handle: JobHandle) -> dict[str, Any]:
        handle.set_label(f"Importing {file_name}...")
        return await run_import(
            storage_key,
            file_name=file_name,
            account_id=account_id,
            owner_user_id=owner_user_id,
        )

    return get_job_runner().start(
        f"finance-import:{file_name}", work, label=f"Uploading {file_name}..."
    )


async def start_import(
    storage_key: str,
    *,
    file_name: str,
    account_id: int | None,
    owner_user_id: int | None,
) -> str:
    """Hand it to the worker, or run it here when there is none.

    A stack without a worker still imports files; it just cannot survive
    a restart mid-run, which is the state this whole module exists to
    get out of. The caller never learns which lane it took - the job id
    answers the same either way.
    """
    try:
        return await _hand_to_worker(
            storage_key,
            file_name=file_name,
            account_id=account_id,
            owner_user_id=owner_user_id,
        )
    except Exception as exc:
        logger.warning(
            "finance.import.no_worker", error=str(exc), file_name=file_name
        )
        return start_import_in_process(
            storage_key,
            file_name=file_name,
            account_id=account_id,
            owner_user_id=owner_user_id,
        )


async def _hand_to_worker(
    storage_key: str,
    *,
    file_name: str,
    account_id: int | None,
    owner_user_id: int | None,
) -> str:
    """Record the job in the shared store, then enqueue it."""
    import uuid

    from app.core.config import settings
    from app.services.system.job_store import RedisJobStore

    job_id = uuid.uuid4().hex
    store = RedisJobStore.from_url(settings.REDIS_URL)
    try:
        await store.create(job_id, f"finance-import:{file_name}", "Queued...")
        try:
            await _enqueue(job_id, storage_key, file_name, account_id, owner_user_id)
        except Exception as exc:
            # The record outlives this function, so a failed hand-off has
            # to be written into it: otherwise the job sits "Queued..."
            # until it expires, with the reason only in these logs.
            await store.fail(job_id, f"Could not queue the import: {exc}")
            raise
    finally:
        await store.aclose()
    logger.info("finance.import.enqueued", job_id=job_id, file_name=file_name)
    return job_id


async def _enqueue(
    job_id: str,
    storage_key: str,
    file_name: str,
    account_id: int | None,
    owner_user_id: int | None,
) -> None:
    from app.components.worker.pools import get_queue_pool

    pool, queue_name = await get_queue_pool("system")
    await pool.enqueue_job(
        "finance_import_task",
        job_id,
        storage_key,
        file_name,
        account_id,
        owner_user_id,
        _queue_name=queue_name,
    )


async def run_import_job(
    job_id: str,
    storage_key: str,
    file_name: str,
    account_id: int | None,
    owner_user_id: int | None,
) -> dict[str, Any]:
    """The worker's entry: narrate into the shared job store, then finish."""
    from app.core.config import settings
    from app.services.system.job_store import RedisJobStore

    store = RedisJobStore.from_url(settings.REDIS_URL)
    try:
        await store.set_label(job_id, f"Importing {file_name}...")
        result = await run_import(
            storage_key,
            file_name=file_name,
            account_id=account_id,
            owner_user_id=owner_user_id,
        )
        await store.finish(job_id, result)
        return result
    except Exception as exc:
        await store.fail(job_id, str(exc) or type(exc).__name__)
        raise
    finally:
        await asyncio.shield(store.aclose())
