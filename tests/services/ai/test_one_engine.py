"""The chat store and the usage ledger run on the async engine like every
other store in the app.

They were the last request-path code on the sync engine, and SQLite's
30-second busy wait taken on the event loop froze the whole webserver
(2026-09-16: Redis, the dashboard's SSE and the health check's own HTTP
call all timed out at once under a slow Ollama turn). A threadpool wrap
was the emergency fix; one engine is the real one, and this keeps it.
"""

import inspect

from app.services.ai import usage_recording
from app.services.ai.domains.chat import conversation, llm_catalog_context
from app.services.ai.service import contexts, status, usage


def test_the_ai_store_never_opens_a_sync_session() -> None:
    for module in (
        conversation,
        llm_catalog_context,
        usage_recording,
        usage,
        status,
        contexts,
    ):
        source = inspect.getsource(module)
        assert "db_session" not in source, module.__name__
        assert "SessionLocal" not in source, module.__name__
        assert "run_in_threadpool" not in source, module.__name__
        assert "Session(engine)" not in source, module.__name__
