"""Whether it is you, the bank, or Steward (#585).

When a balance is wrong the first question is whose fault it is. The
answer was spread over the connection cards, Activity and Overseer; the
Status tab reads them together, derived from what the app records and
never typed by hand.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.time import utcnow
from tests.web.dom import none, one, select, text

STATUS = "/settings/status"


@pytest.fixture(autouse=True)
def unhealthy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The health tree, without walking it: the parts that are unwell."""
    sick: list[str] = []

    async def status() -> SimpleNamespace:
        return SimpleNamespace(overall_healthy=not sick, unhealthy_components=sick)

    monkeypatch.setattr(
        "app.components.web_frontend.routes.finance.status.get_system_status", status
    )
    return sick


async def _runs(db: AsyncSession, *states: str) -> None:
    from app.services.finance.models.imports import FinanceImportBatch

    for state in states:
        db.add(
            FinanceImportBatch(
                owner_user_id=0,
                source_type="plaid_sync",
                status=state,
                started_at=utcnow() - timedelta(hours=1),
            )
        )
    await db.commit()


def _row(page: str, key: str) -> str:
    return text(one(page, f'[data-service="{key}"]'))


class TestTheStatusTab:
    @pytest.mark.queryspy(threshold=3)  # page and fragment
    def test_it_is_a_settings_tab_drawn_both_ways(
        self, client: TestClient, hx: TestClient
    ) -> None:
        page = client.get(STATUS).text
        marked = one(page, '#settings-nav a[aria-current="page"]')
        assert marked.get("href") == STATUS
        one(hx.get(STATUS).text, "#status")

    def test_all_good_says_so(self, client: TestClient) -> None:
        page = client.get(STATUS).text
        assert text(one(page, "[data-headline]")) == "All good"
        none(page, "[data-need]")
        assert "Operational" in _row(page, "steward")


class TestWhoseFault:
    async def test_a_bank_that_needs_you_heads_the_page_with_its_fix(
        self, client: TestClient, async_db_session: AsyncSession, connection: int
    ) -> None:
        from app.services.finance.models import FinanceConnection

        row = await async_db_session.get(FinanceConnection, connection)
        assert row is not None
        row.status = "error"
        row.status_detail = (
            "Connection to Optum Bank HSA may need attention. Auth required"
        )
        row.last_successful_sync_at = utcnow()
        async_db_session.add(row)
        await async_db_session.commit()

        page = client.get(STATUS).text
        assert text(one(page, "[data-headline]")) == "Needs you"
        need = one(page, "#status-needs [data-need]")
        assert "Optum Bank HSA needs you to sign in again" in text(need)
        one(need, f'[hx-post="/settings/connections/{connection}/reconnect"]')

    async def test_a_provider_reads_its_share_of_failed_syncs(
        self, client: TestClient, async_db_session: AsyncSession, connection: int
    ) -> None:
        await _runs(async_db_session, "committed", "failed")

        page = client.get(STATUS).text
        assert "50% of syncs failed" in _row(page, "plaid")
        assert text(one(page, "[data-headline]")) == "Degraded"

    async def test_a_provider_whose_syncs_all_failed_is_down(
        self, client: TestClient, async_db_session: AsyncSession, connection: int
    ) -> None:
        await _runs(async_db_session, "failed", "failed")

        assert "Down" in _row(client.get(STATUS).text, "plaid")

    async def test_a_provider_whose_syncs_worked_is_operational(
        self, client: TestClient, async_db_session: AsyncSession, connection: int
    ) -> None:
        await _runs(async_db_session, "committed")

        assert "Operational" in _row(client.get(STATUS).text, "plaid")

    def test_steward_names_only_the_part_that_is_unwell(
        self, client: TestClient, unhealthy: list[str]
    ) -> None:
        """A sick part sickens every part above it; naming the root says
        nothing."""
        unhealthy.extend(["aegis", "aegis.components", "aegis.components.worker"])

        page = client.get(STATUS).text
        assert _row(page, "steward").endswith("Degraded: worker")
        assert text(one(page, "[data-headline]")) == "Degraded"


class TestTheLock:
    def test_the_health_walk_runs_before_the_page_reads(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every session begins IMMEDIATE, so the page's own session IS the
        write lock from its first read. The health checks open sessions of
        their own; walked after the page had read, each waited out the
        timeout behind the page waiting on it, and every other process
        queued behind both (32 seconds, then "database is locked")."""
        from app.components.web_frontend.routes.finance import status as page

        order: list[str] = []
        read = page.links

        async def status() -> SimpleNamespace:
            order.append("health")
            return SimpleNamespace(overall_healthy=True, unhealthy_components=[])

        async def links(*args: Any, **kwargs: Any) -> list[Any]:
            order.append("read")
            return await read(*args, **kwargs)

        monkeypatch.setattr(page, "get_system_status", status)
        monkeypatch.setattr(page, "links", links)

        client.get(STATUS)

        assert order == ["health", "read"]


class TestTheBankSync:
    def test_a_dev_stack_leaves_it_to_the_server(self, client: TestClient) -> None:
        """The sync is ``server_only`` (#532): here it never runs."""
        assert "Runs on the server" in _row(client.get(STATUS).text, "sync")

    async def test_it_reads_its_last_real_run(
        self,
        client: TestClient,
        async_db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core.config import settings
        from app.services.scheduler.models import JobExecution

        monkeypatch.setattr(settings, "APP_ENV", "production")
        async_db_session.add(
            JobExecution(
                job_id="finance_sync_connections",
                started_at=utcnow() - timedelta(hours=2),
                status="failed",
            )
        )
        await async_db_session.commit()

        assert "Failed" in _row(client.get(STATUS).text, "sync")

    def test_history_is_a_link_away(self, client: TestClient) -> None:
        links = select(client.get(STATUS).text, '#status a[href="/settings/activity"]')
        assert links
