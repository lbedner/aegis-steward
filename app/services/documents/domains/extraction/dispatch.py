"""Where an extraction job runs: the worker when the stack has one, else here.

The Extract button, the jobs API and the SSE stream never know which. A
job id comes back either way; progress and the result arrive through the
same endpoints.
"""

from __future__ import annotations

from app.services.documents.domains.extraction.jobs import run_extraction
from app.services.system.jobs import JobHandle, get_job_runner


async def start_extraction_in_process(
    document_id: int, *, owner_user_id: int | None, force: bool
) -> str:
    """Run the extraction as an asyncio task in this process."""

    async def work(handle: JobHandle) -> dict[str, int]:
        return await run_extraction(
            document_id,
            owner_user_id=owner_user_id,
            force=force,
            report=handle.set_label,
        )

    return get_job_runner().start(
        f"documents-extract:{document_id}", work, label="Opening the document..."
    )


async def start_extraction(
    document_id: int, *, owner_user_id: int | None, force: bool
) -> str:
    """Record the job in the shared store, then hand it to the worker;
    or return the job already reading this document."""
    import uuid

    from app.core.config import settings
    from app.core.log import logger
    from app.services.system.job_store import RedisJobStore

    name = f"documents-extract:{document_id}"
    store = RedisJobStore.from_url(settings.REDIS_URL)
    try:
        # One reading at a time per document. A second ask while the
        # first is still on the worker (an agent calling twice in three
        # seconds, a page refreshed) joins that job rather than starting
        # another pass over the same scan.
        for job in await store.list_jobs():
            if job.name == name and job.status == "running" and not force:
                return job.job_id
        job_id = uuid.uuid4().hex
        await store.create(job_id, name, "Queued...")
        try:
            await _enqueue(job_id, document_id, owner_user_id, force)
        except Exception as exc:
            # The record outlives this function, so a failed hand-off has to
            # be written into it: otherwise the job sits "Queued..." until it
            # expires, with the reason only in this process's logs.
            await store.fail(job_id, f"Could not queue the extraction: {exc}")
            raise
    finally:
        await store.aclose()
    logger.info("documents.extract.enqueued", job_id=job_id, document_id=document_id)
    return job_id


async def _enqueue(
    job_id: str, document_id: int, owner_user_id: int | None, force: bool
) -> None:
    from app.components.worker.pools import get_queue_pool

    pool, queue_name = await get_queue_pool("system")
    await pool.enqueue_job(
        "extract_document_task",
        job_id,
        document_id,
        owner_user_id,
        force,
        _queue_name=queue_name,
    )


# How long a caller may sit on a reading before it gives the job id back
# instead: a seven-page scan through the vision model is a couple of
# minutes, and the stream says how long it has been quiet the whole time.
WAIT_BUDGET_SECONDS = 180.0
WAIT_POLL_SECONDS = 2.0


async def wait_for_extraction(
    job_id: str, *, budget: float = WAIT_BUDGET_SECONDS
) -> str:
    """Sit on a reading until it lands, fails, or the budget runs out.

    Returns the job's final status - "done", "failed" - or "running" when
    the budget ran out first, so the caller can say the read is still
    going rather than pretend it never started.
    """
    import asyncio

    from app.core.config import settings
    from app.services.system.job_store import RedisJobStore

    store = RedisJobStore.from_url(settings.REDIS_URL)
    try:
        waited = 0.0
        while waited < budget:
            job = await store.get(job_id)
            if job is None or job.status != "running":
                return job.status if job else "done"
            await asyncio.sleep(WAIT_POLL_SECONDS)
            waited += WAIT_POLL_SECONDS
        return "running"
    finally:
        await store.aclose()
