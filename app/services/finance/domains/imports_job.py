"""Where a file import runs: the worker when the stack has one, else here.

An 18,000-row export takes seconds, which is exactly why it looked safe
to run inside the web process - until a restart landed on one. The job
died with the process, the follower kept spinning, and nothing said so.
A worker outlives a reload; the same job id comes back either way and
the jobs API cannot tell the difference.

The bytes travel through storage rather than Redis: the worker mounts
the same volume, an upload is capped at ``IMPORT_MAX_BYTES``, and a
queue is a poor place to park a file.

The review before an import reads the same file, and so do an investment
ledger's review and commit: an 18,000-row plan measured 253 MiB, a small
server's whole web process (#487). Each is a ``kind`` of run here, on the
same lanes, followed the same way.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, NamedTuple

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import get_async_session
from app.core.log import logger
from app.services.system.jobs import JobHandle, SetLabel, get_job_runner

if TYPE_CHECKING:
    from app.services.finance.domains.investments.activity import (
        InvestmentActivity,
    )
    from app.services.finance.schemas import ImportPreviewResponse

# A single-account file reviewed with no target, or a ledger with none:
# said as the job's error, so the frame puts the file picker back. The
# import route refuses a ledger with none before it is stored at all.
NEEDS_ACCOUNT = "This file carries no account; choose the account it belongs to."


async def _import(
    session: AsyncSession,
    data: bytes,
    file_name: str,
    account_id: int | None,
    owner_user_id: int | None,
    on_label: SetLabel | None,
) -> dict[str, Any]:
    from app.services.documents.domains.reading import receipts
    from app.services.finance.schemas import import_result_payload
    from app.services.finance.service import FinanceService

    result = await FinanceService(session).import_file(
        owner_user_id=owner_user_id,
        file_name=file_name,
        file_bytes=data,
        account_id=account_id,
        on_label=on_label,
    )
    # A charge a waiting receipt is for may be in the file (#330).
    await receipts.match_waiting(session, owner_user_id=owner_user_id)
    await session.commit()
    return import_result_payload(result)


async def _preview(
    session: AsyncSession,
    data: bytes,
    file_name: str,
    account_id: int | None,
    owner_user_id: int | None,
    on_label: SetLabel | None,
) -> dict[str, Any]:
    """The dry run: the plan the import would execute, writing nothing."""
    from app.services.finance.service import FinanceService

    service = FinanceService(session)
    plan = await service.preview_file(
        owner_user_id=owner_user_id,
        file_name=file_name,
        file_bytes=data,
        account_id=account_id,
    )
    if plan.needs_account:
        raise ValueError(NEEDS_ACCOUNT)
    return capped_review(await service.preview_payload(plan))


def capped_review(review: ImportPreviewResponse) -> dict[str, Any]:
    """The review as its job records it: the frame names only
    ``PREVIEW_DETAIL_CAP`` updates and new categories, so the job record
    in Redis holds no more. "And N more" counts from ``rows_updated`` and
    ``new_categories_total``. The JSON API's review stays whole."""
    from app.services.finance.constants import PREVIEW_DETAIL_CAP

    capped = review.model_copy(
        update={
            "edits": review.edits[:PREVIEW_DETAIL_CAP],
            "new_categories": review.new_categories[:PREVIEW_DETAIL_CAP],
        }
    )
    return {
        **capped.model_dump(mode="json"),
        "new_categories_total": len(review.new_categories),
    }


async def _ledger(data: bytes) -> list[InvestmentActivity]:
    """An Optum export parsed off the event loop: a whole file of rows."""
    from app.services.finance.domains.investments.optum import (
        parse_optum_settled_transactions,
    )

    return await asyncio.to_thread(parse_optum_settled_transactions, data)


async def _investments_preview(
    session: AsyncSession,
    data: bytes,
    file_name: str,
    account_id: int | None,
    owner_user_id: int | None,
    on_label: SetLabel | None,
) -> dict[str, Any]:
    from app.services.finance.domains.investments.activity import preview_ledger

    return preview_ledger(await _ledger(data)).model_dump(mode="json")


async def _investments(
    session: AsyncSession,
    data: bytes,
    file_name: str,
    account_id: int | None,
    owner_user_id: int | None,
    on_label: SetLabel | None,
) -> dict[str, Any]:
    """Commit a ledger into the account the request already checked."""
    from app.services.finance.domains.investments.loader import load_ledger
    from app.services.finance.models import FinanceAccount

    account = await session.get(FinanceAccount, account_id) if account_id else None
    if account is None:
        raise ValueError(NEEDS_ACCOUNT)
    result = await load_ledger(
        session,
        owner_user_id=owner_user_id,
        account=account,
        activities=await _ledger(data),
    )
    await session.commit()
    return result.model_dump(mode="json")


Work = Callable[
    [AsyncSession, bytes, str, int | None, int | None, SetLabel | None],
    Awaitable[dict[str, Any]],
]


class Run(NamedTuple):
    work: Work
    label: str  # what it says while doing it
    frame: str  # the template its finished job renders


RUNS: dict[str, Run] = {
    "import": Run(_import, "Importing", "partials/imports/summary.html"),
    "preview": Run(_preview, "Reading", "partials/imports/preview.html"),
    "investments-preview": Run(
        _investments_preview, "Reading", "partials/imports/preview_investments.html"
    ),
    "investments": Run(
        _investments, "Importing", "partials/imports/summary_investments.html"
    ),
}

_PREFIX = "finance-"


def job_name(kind: str, file_name: str) -> str:
    """What the job store calls it: ``frame`` reads the kind back out."""
    return f"{_PREFIX}{kind}:{file_name}"


def frame(name: str | None) -> str | None:
    """The template a finished job named ``name`` renders, or None when
    it is not one of these runs (``partials/jobs/status.html``)."""
    head, colon, _ = (name or "").partition(":")
    if not colon or not head.startswith(_PREFIX):
        return None
    run = RUNS.get(head.removeprefix(_PREFIX))
    return run.frame if run else None


def _label(kind: str, file_name: str) -> str:
    return f"{RUNS[kind].label} {file_name}..."


async def run_import(
    storage_key: str,
    *,
    file_name: str,
    account_id: int | None,
    owner_user_id: int | None,
    kind: str,
    on_label: SetLabel | None = None,
) -> dict[str, Any]:
    """The run itself, in its own session, wherever it is running.

    ``on_label`` is how the run narrates itself to whoever is watching.
    Both lanes have somewhere to put a label already - the worker's shared
    job store and the in-process runner's ``JobHandle`` - and the SSE
    follower renders whichever it finds, so the ingest is handed one
    callable and never learns which lane it is in.
    """
    from app.core.storage import get_storage

    work = RUNS[kind].work
    data = await get_storage().get(storage_key)
    if data is None:
        raise ValueError("The uploaded file is no longer there.")
    # First line the worker writes for this file. Paired with
    # ``finance.import.finished``, a run with only this one is a run whose
    # process died - which is how the 2026-09-19 OOM went unread.
    logger.info(
        "finance.import.started", file_name=file_name, kind=kind, bytes=len(data)
    )
    async with get_async_session() as session:
        return await work(session, data, file_name, account_id, owner_user_id, on_label)


def start_import_in_process(
    storage_key: str,
    *,
    file_name: str,
    account_id: int | None,
    owner_user_id: int | None,
    kind: str,
) -> str:
    """Run it as an asyncio task here. Dies with the process, which is
    why it is the fallback rather than the default."""

    async def work(handle: JobHandle) -> dict[str, Any]:
        handle.set_label(_label(kind, file_name))
        return await run_import(
            storage_key,
            file_name=file_name,
            account_id=account_id,
            owner_user_id=owner_user_id,
            on_label=handle.label_writer(),
            kind=kind,
        )

    return get_job_runner().start(
        job_name(kind, file_name), work, label=f"Uploading {file_name}..."
    )


async def start_import(
    storage_key: str,
    *,
    file_name: str,
    account_id: int | None,
    owner_user_id: int | None,
    kind: str,
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
            kind=kind,
        )
    except Exception as exc:
        logger.warning("finance.import.no_worker", error=str(exc), file_name=file_name)
        return start_import_in_process(
            storage_key,
            file_name=file_name,
            account_id=account_id,
            owner_user_id=owner_user_id,
            kind=kind,
        )


async def _hand_to_worker(
    storage_key: str,
    *,
    file_name: str,
    account_id: int | None,
    owner_user_id: int | None,
    kind: str,
) -> str:
    """Record the job in the shared store, then enqueue it."""
    import uuid

    from app.core.config import settings
    from app.services.system.job_store import RedisJobStore

    job_id = uuid.uuid4().hex
    store = RedisJobStore.from_url(settings.REDIS_URL)
    try:
        await store.create(job_id, job_name(kind, file_name), "Queued...")
        try:
            await _enqueue(
                job_id, storage_key, file_name, account_id, owner_user_id, kind
            )
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
    kind: str,
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
        kind,
        _queue_name=queue_name,
    )


async def run_import_job(
    job_id: str,
    storage_key: str,
    file_name: str,
    account_id: int | None,
    owner_user_id: int | None,
    kind: str,
) -> dict[str, Any]:
    """The worker's entry: narrate into the shared job store, then finish."""
    from app.core.config import settings
    from app.services.system.job_store import RedisJobStore

    store = RedisJobStore.from_url(settings.REDIS_URL)
    try:
        await store.set_label(job_id, _label(kind, file_name))
        result = await run_import(
            storage_key,
            file_name=file_name,
            account_id=account_id,
            owner_user_id=owner_user_id,
            on_label=store.label_writer(job_id),
            kind=kind,
        )
        await store.finish(job_id, result)
        return result
    except Exception as exc:
        await store.fail(job_id, str(exc) or type(exc).__name__)
        raise
    finally:
        await asyncio.shield(store.aclose())
