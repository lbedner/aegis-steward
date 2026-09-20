"""Where a mail import runs: the worker when the stack has one, else here.

The same two lanes the finance import takes, for the same reason - an
export of ten thousand messages outlives any request, and a restart
landing on it must not kill it. The bytes travel through storage; the
queue carries the key.
"""

from __future__ import annotations

import pytest


class TestTheTwoLanes:
    @pytest.mark.asyncio
    async def test_it_goes_to_the_worker_with_the_key_not_the_bytes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.mail import jobs

        sent: dict[str, object] = {}

        async def _enqueue(job_id, storage_key, file_name, owner):
            sent.update(job_id=job_id, storage_key=storage_key, file_name=file_name)

        class _Store:
            async def create(self, *a, **k) -> None: ...
            async def fail(self, *a, **k) -> None: ...
            async def aclose(self) -> None: ...

        monkeypatch.setattr(jobs, "_enqueue", _enqueue)
        monkeypatch.setattr(
            "app.services.system.job_store.RedisJobStore.from_url", lambda url: _Store()
        )

        job_id = await jobs.start_mail_import(
            "store/abc123", file_name="export.mbox", owner_user_id=None
        )

        assert sent["job_id"] == job_id
        assert sent["storage_key"] == "store/abc123"
        assert sent["file_name"] == "export.mbox"

    @pytest.mark.asyncio
    async def test_no_worker_still_imports_here(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.mail import jobs

        async def _no_worker(*a, **k):
            raise RuntimeError("no redis here")

        started: dict[str, object] = {}

        def _in_process(storage_key, **kwargs):
            started.update(storage_key=storage_key, **kwargs)
            return "local-job"

        monkeypatch.setattr(jobs, "_hand_to_worker", _no_worker)
        monkeypatch.setattr(jobs, "start_mail_import_in_process", _in_process)

        job_id = await jobs.start_mail_import(
            "store/abc123", file_name="export.mbox", owner_user_id=None
        )

        assert job_id == "local-job"
        assert started["storage_key"] == "store/abc123"

    def test_the_job_is_named_so_the_follower_can_tell_it_apart(self) -> None:
        """``partials/jobs/status.html`` renders a terminal frame per kind
        of job (trap 3.2 in the handoff): the import's frame took a
        finished document READ to Accounts. This one has to be tellable
        from both."""
        from app.services.mail import jobs

        assert jobs.job_name("export.mbox").startswith("mail-import:")


class TestTheWorkerTask:
    def test_it_is_registered_on_the_system_queue(self) -> None:
        from app.components.worker.queues.system import WorkerSettings
        from app.components.worker.tasks.mail_tasks import mail_import_task

        assert mail_import_task in WorkerSettings.functions
