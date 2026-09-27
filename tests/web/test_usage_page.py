"""Settings > Usage (#270): what models and voice cost, on one page.

Both render paths, reading the request's own session.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.components.web_frontend.routes.finance.usage import USAGE
from app.services.ai.models.llm import LLMUsage
from tests.web.dom import none, one, select, text


@pytest.fixture
async def spent(async_db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    async_db_session.add_all(
        [
            LLMUsage(
                action="stream_chat:finance-assistant",
                model_id="gpt-5.6-luna",
                input_tokens=1_000,
                output_tokens=200,
                total_cost=0.02,
                timestamp=now,
            ),
            LLMUsage(
                action="live",
                model_id="gpt-live-1",
                input_tokens=0,
                output_tokens=0,
                total_cost=0.50,
                audio_seconds=600,
                session_id="live_1",
                conversation_id="c-1",
                timestamp=now - timedelta(minutes=30),
            ),
        ]
    )
    await async_db_session.commit()


class TestTheUsagePage:
    @pytest.mark.parametrize("path_client", ["client", "hx"])
    def test_it_totals_by_kind(
        self, request: pytest.FixtureRequest, path_client: str, spent: None
    ) -> None:
        page = request.getfixturevalue(path_client).get(USAGE).text
        kinds = {
            text(cell.cssselect("dt")[0]): text(cell.cssselect("dd")[0])
            for cell in select(page, "#usage-kinds > div")
        }
        assert kinds["Live calls"] == "$0.50"
        assert kinds["Chat and agents"] == "$0.02"

    def test_a_fragment_has_no_shell(self, hx: TestClient, spent: None) -> None:
        none(hx.get(USAGE).text, "html")

    def test_each_call_is_listed_with_its_minutes(
        self, client: TestClient, spent: None
    ) -> None:
        (row,) = select(client.get(USAGE).text, "#usage-calls tbody tr")
        assert "10.0" in text(row) and "$0.50" in text(row)

    def test_it_is_a_settings_tab(self, client: TestClient, spent: None) -> None:
        tab = one(client.get(USAGE).text, "#settings-nav [aria-current=page]")
        assert text(tab) == "Usage"

    def test_the_window_narrows(self, client: TestClient, spent: None) -> None:
        one(client.get(f"{USAGE}?days=7").text, "input[name=days][value='7'][checked]")
