"""The assistant's accounts() tool, against sessions it really opens.

Kept out of ``test_finance_ai_tools``, whose autouse fixture hands every
tool one test session that never closes - which is exactly what hid a
read through a closed session (#238).
"""

import pytest
from sqlalchemy import event


@pytest.mark.asyncio
async def test_accounts_opens_one_connection(app_owned_engine) -> None:
    """It read names through its session after the session had closed,
    which quietly opened a second connection nothing returned. That one
    held the write lock until garbage collection terminated it,
    mid-answer (2026-09-23)."""
    from app.services.finance.ai_account_tools import accounts

    opened: list[int] = []
    # The engine get_async_session() opens in tests (conftest).
    pool = app_owned_engine.sync_engine.pool

    def checked_out(_dbapi: object, record: object, _proxy: object) -> None:
        opened.append(id(record))

    event.listen(pool, "checkout", checked_out)
    try:
        await accounts()
    finally:
        event.remove(pool, "checkout", checked_out)

    assert len(opened) == 1
