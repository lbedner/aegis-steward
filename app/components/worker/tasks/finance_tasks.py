"""Finance work too long for a request, as worker tasks (arq form):
importing a file, and syncing one connection.

Registered in the system queue so no extra worker process is needed. The
body imports the finance service lazily: a worker stack without finance
still imports this module and simply never receives the task.
"""

from typing import Any


async def finance_import_task(
    ctx: dict[str, Any],
    job_id: str,
    storage_key: str,
    file_name: str,
    account_id: int | None,
    owner_user_id: int | None,
) -> dict[str, Any]:
    from app.services.finance.domains.imports_job import run_import_job

    return await run_import_job(
        job_id, storage_key, file_name, account_id, owner_user_id
    )


async def finance_sync_connection_task(
    ctx: dict[str, Any], connection_id: int, owner_user_id: int | None
) -> dict[str, Any]:
    """One connection, synced from where it stands: after accounts are
    placed (``connections.placing``), the whole history they held."""
    from app.core.db import get_async_session
    from app.services.documents.domains.reading import filing
    from app.services.finance.adapters.providers import connections

    async with get_async_session() as db:
        result = await connections.sync_one_connection(
            db, connection_id, owner_user_id=owner_user_id
        )
        # A bank just said what its accounts are called and numbered,
        # and who it is: what an unfiled statement may print (#409).
        await filing.reread_unfiled(db, owner_user_id=owner_user_id)
        await db.commit()
    return {"added": result.added if result else 0}
