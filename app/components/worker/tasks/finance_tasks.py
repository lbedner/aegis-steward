"""Importing a file as a worker task (arq form).

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
