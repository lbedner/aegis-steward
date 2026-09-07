"""
Per-task history persistence using Redis Hashes and Sorted Sets.

Records task lifecycle events (enqueued, started, finished) alongside
the existing Redis Stream event publishing. Provides both async and sync
variants so all three worker backends (arq, TaskIQ, Dramatiq) can use it.

Redis structures:
- ``aegis:task:{job_id}`` — Hash with task metadata
- ``aegis:tasks:queue:{queue_name}`` — Sorted Set indexed by enqueue timestamp
"""

from datetime import UTC, datetime
from typing import Any

from app.core.log import logger

# Key prefixes
_TASK_KEY_PREFIX = "aegis:task:"
_QUEUE_INDEX_PREFIX = "aegis:tasks:queue:"


# ---------------------------------------------------------------------------
# Task name resolution helpers (backend-specific)
# ---------------------------------------------------------------------------


def resolve_task_docstring(task_name: str) -> str:
    """Look up a task function's docstring from the registry.

    Returns the first line of the docstring, or an empty string.
    """
    try:
        from app.components.worker.tasks import get_task_by_name

        func = get_task_by_name(task_name)
        if func and func.__doc__:
            # Return first non-empty line of the docstring
            for line in func.__doc__.strip().splitlines():
                stripped = line.strip()
                if stripped:
                    return stripped
    except Exception:
        pass
    return ""


def _enrich_mapping(mapping: dict[str, str], task_name: str | None) -> None:
    """Add task name and docstring to a mapping if available."""
    if task_name:
        mapping["name"] = task_name
        doc = resolve_task_docstring(task_name)
        if doc:
            mapping["description"] = doc


async def resolve_arq_task_name(redis: Any, job_id: str) -> str | None:
    """Extract function name for an arq job.

    Tries the job key first (available during/before execution),
    then falls back to the result key (available after execution).
    arq's hooks don't include the function name in ``ctx``, so we
    read it directly from Redis.
    """
    try:
        from arq.constants import job_key_prefix, result_key_prefix
        from arq.jobs import deserialize_job_raw, deserialize_result

        # Try job key first (still exists during on_job_start)
        raw = await redis.get(job_key_prefix + job_id)
        if raw:
            function_name, *_ = deserialize_job_raw(raw)
            return function_name

        # Fall back to result key (exists after job completes)
        raw = await redis.get(result_key_prefix + job_id)
        if raw:
            result = deserialize_result(raw)
            return result.function  # type: ignore[return-value]
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Async variants (arq, TaskIQ, API reads)
# ---------------------------------------------------------------------------


async def record_task_enqueued(
    redis: Any,
    job_id: str,
    task_name: str,
    queue_name: str,
    ttl_seconds: int = 86400,
) -> None:
    """Record a task being enqueued.

    Creates a Hash with initial metadata and adds the job to the
    per-queue sorted set.
    """
    now = datetime.now(UTC).isoformat()
    key = f"{_TASK_KEY_PREFIX}{job_id}"
    try:
        mapping: dict[str, str] = {
            "job_id": job_id,
            "queue": queue_name,
            "status": "enqueued",
            "enqueued_at": now,
        }
        _enrich_mapping(mapping, task_name)
        await redis.hset(key, mapping=mapping)
        await redis.expire(key, ttl_seconds)
        await redis.zadd(
            f"{_QUEUE_INDEX_PREFIX}{queue_name}",
            {job_id: datetime.now(UTC).timestamp()},
        )
    except Exception as e:
        logger.debug(f"Failed to record task enqueued: {e}")


async def record_task_started(
    redis: Any,
    job_id: str,
    task_name: str | None = None,
    queue_name: str | None = None,
    ttl_seconds: int = 86400,
) -> None:
    """Mark a task as started. Creates the record if it doesn't exist yet."""
    key = f"{_TASK_KEY_PREFIX}{job_id}"
    now = datetime.now(UTC)
    try:
        exists = await redis.exists(key)
        if not exists:
            # Create record on-the-fly (task was enqueued without recording)
            mapping: dict[str, str] = {
                "job_id": job_id,
                "status": "running",
                "started_at": now.isoformat(),
                "enqueued_at": now.isoformat(),
            }
            _enrich_mapping(mapping, task_name)
            if queue_name:
                mapping["queue"] = queue_name
            await redis.hset(key, mapping=mapping)
            await redis.expire(key, ttl_seconds)
            if queue_name:
                await redis.zadd(
                    f"{_QUEUE_INDEX_PREFIX}{queue_name}",
                    {job_id: now.timestamp()},
                )
        else:
            mapping = {
                "status": "running",
                "started_at": now.isoformat(),
            }
            _enrich_mapping(mapping, task_name)
            if queue_name:
                mapping["queue"] = queue_name
            await redis.hset(key, mapping=mapping)
    except Exception as e:
        logger.debug(f"Failed to record task started: {e}")


async def record_task_finished(
    redis: Any,
    job_id: str,
    success: bool,
    error: str | None = None,
    task_name: str | None = None,
    queue_name: str | None = None,
    ttl_seconds: int = 86400,
) -> None:
    """Mark a task as finished (success or failure). Creates record if missing."""
    key = f"{_TASK_KEY_PREFIX}{job_id}"
    try:
        now = datetime.now(UTC)
        exists = await redis.exists(key)
        if not exists:
            # Create record on-the-fly for tasks that bypassed enqueue recording
            mapping: dict[str, str] = {
                "job_id": job_id,
                "status": "completed" if success else "failed",
                "finished_at": now.isoformat(),
                "enqueued_at": now.isoformat(),
            }
            _enrich_mapping(mapping, task_name)
            if queue_name:
                mapping["queue"] = queue_name
            if error:
                mapping["error"] = str(error)[:2000]
            await redis.hset(key, mapping=mapping)
            await redis.expire(key, ttl_seconds)
            if queue_name:
                await redis.zadd(
                    f"{_QUEUE_INDEX_PREFIX}{queue_name}",
                    {job_id: now.timestamp()},
                )
            return

        mapping = {
            "status": "completed" if success else "failed",
            "finished_at": now.isoformat(),
        }
        _enrich_mapping(mapping, task_name)
        # Compute duration if started_at exists
        started_raw = await redis.hget(key, "started_at")
        if started_raw:
            started_str = (
                started_raw if isinstance(started_raw, str) else started_raw.decode()
            )
            started = datetime.fromisoformat(started_str)
            duration_ms = (now - started).total_seconds() * 1000
            mapping["duration_ms"] = f"{duration_ms:.1f}"
        if error:
            mapping["error"] = str(error)[:2000]
        await redis.hset(key, mapping=mapping)
    except Exception as e:
        logger.debug(f"Failed to record task finished: {e}")


async def get_task_record(redis: Any, job_id: str) -> dict[str, str] | None:
    """Retrieve a single task record by job ID."""
    key = f"{_TASK_KEY_PREFIX}{job_id}"
    try:
        data = await redis.hgetall(key)
        if not data:
            return None
        # Decode bytes keys/values if needed
        return {
            (k if isinstance(k, str) else k.decode()): (
                v if isinstance(v, str) else v.decode()
            )
            for k, v in data.items()
        }
    except Exception as e:
        logger.debug(f"Failed to get task record: {e}")
        return None


async def _pipeline_get_records(redis: Any, job_ids: list[str]) -> list[dict[str, str]]:
    """Fetch multiple task records in a single Redis pipeline round trip."""
    if not job_ids:
        return []
    pipe = redis.pipeline(transaction=False)
    for jid in job_ids:
        pipe.hgetall(f"{_TASK_KEY_PREFIX}{jid}")
    results = await pipe.execute()
    tasks: list[dict[str, str]] = []
    for data in results:
        if not data:
            continue
        record = {
            (k if isinstance(k, str) else k.decode()): (
                v if isinstance(v, str) else v.decode()
            )
            for k, v in data.items()
        }
        tasks.append(record)
    return tasks


async def _pipeline_get_statuses(redis: Any, job_ids: list[str]) -> list[str | None]:
    """Fetch only the status field for multiple tasks in one pipeline."""
    if not job_ids:
        return []
    pipe = redis.pipeline(transaction=False)
    for jid in job_ids:
        pipe.hget(f"{_TASK_KEY_PREFIX}{jid}", "status")
    results = await pipe.execute()
    return [(r if isinstance(r, str) else r.decode()) if r else None for r in results]


async def get_queue_stats(
    redis: Any,
    queue_name: str,
    limit: int = 0,
) -> dict[str, int]:
    """Count tasks by status for a queue.

    Args:
        redis: Async Redis client.
        queue_name: Queue name to count stats for.
        limit: If > 0, only scan the most recent N tasks (faster for health checks).
               If 0, scan all tasks.

    Returns:
        Dict with keys: running, completed, failed, total.
    """
    index_key = f"{_QUEUE_INDEX_PREFIX}{queue_name}"
    try:
        if limit > 0:
            all_ids_raw = await redis.zrevrange(index_key, 0, limit - 1)
        else:
            all_ids_raw = await redis.zrange(index_key, 0, -1)
        if not all_ids_raw:
            return {"running": 0, "completed": 0, "failed": 0, "total": 0}

        all_ids = [j if isinstance(j, str) else j.decode() for j in all_ids_raw]
        statuses = await _pipeline_get_statuses(redis, all_ids)

        counts = {"running": 0, "completed": 0, "failed": 0, "total": len(all_ids)}
        for s in statuses:
            if s in counts:
                counts[s] += 1
        return counts
    except Exception as e:
        logger.debug(f"Failed to get queue stats: {e}")
        return {"running": 0, "completed": 0, "failed": 0, "total": 0}


async def list_tasks_by_queue(
    redis: Any,
    queue_name: str,
    offset: int = 0,
    limit: int = 50,
    order: str = "desc",
    status: str | None = None,
) -> tuple[list[dict[str, str]], int]:
    """List task records for a queue with pagination.

    Lazily removes expired members from the sorted set.
    Uses Redis pipelines to minimise round trips.
    When ``status`` is provided, records are post-filtered and the
    returned total reflects only matching records.

    Returns:
        Tuple of (task records, total count).
    """
    index_key = f"{_QUEUE_INDEX_PREFIX}{queue_name}"
    try:
        # Lazy cleanup: remove members whose hash has expired
        await _cleanup_expired_members(redis, index_key)

        if status:
            # Status filter: pipeline-fetch statuses, filter, then paginate
            if order == "desc":
                all_ids_raw = await redis.zrevrange(index_key, 0, -1)
            else:
                all_ids_raw = await redis.zrange(index_key, 0, -1)

            all_ids = [j if isinstance(j, str) else j.decode() for j in all_ids_raw]
            statuses = await _pipeline_get_statuses(redis, all_ids)
            matching_ids = [
                jid for jid, s in zip(all_ids, statuses, strict=False) if s == status
            ]
            total = len(matching_ids)
            page_ids = matching_ids[offset : offset + limit]
            tasks = await _pipeline_get_records(redis, page_ids)
        else:
            total = await redis.zcard(index_key)

            if order == "desc":
                job_ids_raw = await redis.zrevrange(
                    index_key, offset, offset + limit - 1
                )
            else:
                job_ids_raw = await redis.zrange(index_key, offset, offset + limit - 1)

            job_ids = [j if isinstance(j, str) else j.decode() for j in job_ids_raw]
            tasks = await _pipeline_get_records(redis, job_ids)
        return tasks, total
    except Exception as e:
        logger.debug(f"Failed to list tasks by queue: {e}")
        return [], 0


async def cleanup_old_tasks(
    redis: Any,
    queue_name: str,
    max_age_seconds: int,
) -> int:
    """Remove tasks older than max_age_seconds from the sorted set.

    Hash keys auto-expire via TTL; this cleans up the sorted set index.

    Returns:
        Number of entries removed.
    """
    index_key = f"{_QUEUE_INDEX_PREFIX}{queue_name}"
    try:
        cutoff = datetime.now(UTC).timestamp() - max_age_seconds
        removed: int = await redis.zremrangebyscore(index_key, "-inf", cutoff)
        return removed
    except Exception as e:
        logger.debug(f"Failed to cleanup old tasks: {e}")
        return 0


async def clear_queue_history(redis: Any, queue_name: str) -> int:
    """Delete all task history for a queue.

    Removes hash keys and the sorted set index.

    Returns:
        Number of task records deleted.
    """
    index_key = f"{_QUEUE_INDEX_PREFIX}{queue_name}"
    try:
        job_ids = await redis.zrange(index_key, 0, -1)
        count = 0
        for jid in job_ids:
            jid_str = jid if isinstance(jid, str) else jid.decode()
            await redis.delete(f"{_TASK_KEY_PREFIX}{jid_str}")
            count += 1
        await redis.delete(index_key)
        return count
    except Exception as e:
        logger.debug(f"Failed to clear queue history: {e}")
        return 0


async def _cleanup_expired_members(redis: Any, index_key: str) -> None:
    """Remove sorted set members whose hash key has expired.

    Uses a pipeline to check existence in a single round trip.
    """
    try:
        members = await redis.zrange(index_key, 0, 99)
        if not members:
            return
        member_strs = [m if isinstance(m, str) else m.decode() for m in members]
        pipe = redis.pipeline(transaction=False)
        for m_str in member_strs:
            pipe.exists(f"{_TASK_KEY_PREFIX}{m_str}")
        results = await pipe.execute()
        expired = [
            m for m, exists in zip(member_strs, results, strict=False) if not exists
        ]
        if expired:
            await redis.zrem(index_key, *expired)
    except Exception:
        pass  # best-effort cleanup


# ---------------------------------------------------------------------------
# Sync variants (Dramatiq middleware — runs in worker thread)
# ---------------------------------------------------------------------------


def record_task_enqueued_sync(
    redis: Any,
    job_id: str,
    task_name: str,
    queue_name: str,
    ttl_seconds: int = 86400,
) -> None:
    """Sync variant of record_task_enqueued for Dramatiq."""
    now = datetime.now(UTC).isoformat()
    key = f"{_TASK_KEY_PREFIX}{job_id}"
    try:
        redis.hset(
            key,
            mapping={
                "job_id": job_id,
                "name": task_name,
                "queue": queue_name,
                "status": "enqueued",
                "enqueued_at": now,
            },
        )
        redis.expire(key, ttl_seconds)
        redis.zadd(
            f"{_QUEUE_INDEX_PREFIX}{queue_name}",
            {job_id: datetime.now(UTC).timestamp()},
        )
    except Exception as e:
        logger.debug(f"Failed to record task enqueued (sync): {e}")


def record_task_started_sync(
    redis: Any,
    job_id: str,
    task_name: str | None = None,
    queue_name: str | None = None,
    ttl_seconds: int = 86400,
) -> None:
    """Sync variant of record_task_started for Dramatiq.

    Creates the record on-the-fly if it doesn't exist yet (e.g. sub-tasks
    dispatched via ``actor.send()`` that bypassed ``record_task_enqueued``).
    """
    key = f"{_TASK_KEY_PREFIX}{job_id}"
    now = datetime.now(UTC)
    try:
        if not redis.exists(key):
            mapping: dict[str, str] = {
                "job_id": job_id,
                "status": "running",
                "started_at": now.isoformat(),
                "enqueued_at": now.isoformat(),
            }
            _enrich_mapping(mapping, task_name)
            if queue_name:
                mapping["queue"] = queue_name
            redis.hset(key, mapping=mapping)
            redis.expire(key, ttl_seconds)
            if queue_name:
                redis.zadd(
                    f"{_QUEUE_INDEX_PREFIX}{queue_name}",
                    {job_id: now.timestamp()},
                )
            return
        mapping = {
            "status": "running",
            "started_at": now.isoformat(),
        }
        _enrich_mapping(mapping, task_name)
        if queue_name:
            mapping["queue"] = queue_name
        redis.hset(key, mapping=mapping)
    except Exception as e:
        logger.debug(f"Failed to record task started (sync): {e}")


def record_task_finished_sync(
    redis: Any,
    job_id: str,
    success: bool,
    error: str | None = None,
    task_name: str | None = None,
    queue_name: str | None = None,
    ttl_seconds: int = 86400,
) -> None:
    """Sync variant of record_task_finished for Dramatiq.

    Creates the record on-the-fly if it doesn't exist yet.
    """
    key = f"{_TASK_KEY_PREFIX}{job_id}"
    try:
        now = datetime.now(UTC)
        if not redis.exists(key):
            mapping: dict[str, str] = {
                "job_id": job_id,
                "status": "completed" if success else "failed",
                "finished_at": now.isoformat(),
                "enqueued_at": now.isoformat(),
            }
            _enrich_mapping(mapping, task_name)
            if queue_name:
                mapping["queue"] = queue_name
            if error:
                mapping["error"] = str(error)[:2000]
            redis.hset(key, mapping=mapping)
            redis.expire(key, ttl_seconds)
            if queue_name:
                redis.zadd(
                    f"{_QUEUE_INDEX_PREFIX}{queue_name}",
                    {job_id: now.timestamp()},
                )
            return
        mapping = {
            "status": "completed" if success else "failed",
            "finished_at": now.isoformat(),
        }
        _enrich_mapping(mapping, task_name)
        started_raw = redis.hget(key, "started_at")
        if started_raw:
            started_str = (
                started_raw if isinstance(started_raw, str) else started_raw.decode()
            )
            started = datetime.fromisoformat(started_str)
            duration_ms = (now - started).total_seconds() * 1000
            mapping["duration_ms"] = f"{duration_ms:.1f}"
        if error:
            mapping["error"] = str(error)[:2000]
        redis.hset(key, mapping=mapping)
    except Exception as e:
        logger.debug(f"Failed to record task finished (sync): {e}")
