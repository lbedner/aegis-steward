"""The scheduler decides WHEN; a worker does the work.

Every scheduled job but the heartbeat is enqueued by name onto a worker
queue (``worker/tasks/service_jobs.py``). Running them in this process is
what piled every missed job into one place after host sleep on 2026-09-25,
beside the scheduler's own writes to its job store.
"""

from app.components.worker.pools import get_queue_pool


async def enqueue_task(name: str, queue: str = "system") -> None:
    pool, queue_name = await get_queue_pool(queue)
    await pool.enqueue_job(name, _queue_name=queue_name)
