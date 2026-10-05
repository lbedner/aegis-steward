"""Every worker job runs on the model you picked (#390).

The selection is a database row, switched without a restart. The
webserver and scheduler apply it as they boot; the worker never did, so a
job that built its model from settings alone - the chat summary fold - ran
the ``.env`` bootstrap model, which this install had never pulled
(``404 model 'llama3.2:3b' not found``, 2026-10-05).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.components.worker.queues.system import WorkerSettings


@pytest.mark.asyncio
async def test_a_job_starts_on_the_active_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings
    from app.services.ai.domains.llm import active_model

    asked: list[Any] = []

    async def _sync(given: Any) -> bool:
        asked.append(given)
        return True

    monkeypatch.setattr(active_model, "sync_from_db", _sync)

    await WorkerSettings.on_job_start({})

    assert asked == [settings]
