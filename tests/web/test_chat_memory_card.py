"""A memory the assistant asks to keep is an ordinary approval card (#484):
the chat's change card draws it, and approving there is what saves it."""

from __future__ import annotations

from fastapi.testclient import TestClient
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.ai.domains.chat.user_memory import get_user_memory, save_user_fact
from app.services.finance.service import FinanceService
from tests._session import opens
from tests.web.dom import one, text


@pytest.mark.asyncio
async def test_memory_card_draws_and_approves_in_place(
    hx: TestClient, finance: FinanceService, async_db_session: AsyncSession
) -> None:
    change = await finance.propose_change(
        "memory.save",
        {"user_id": "u1", "fact": "house is worth $711,200", "category": "finance"},
        owner_user_id=None,
    )
    await finance.db.commit()

    card = one(
        hx.get(f"/chat/components/change/{change.id}").text,
        "[data-component=pending_change]",
    )
    assert "house is worth $711,200" in text(card)
    assert text(one(card, "[data-tone=warn]")) == "Awaiting your approval"
    assert await get_user_memory(async_db_session, "u1") is None

    approved = hx.post(f"/chat/components/change/{change.id}/approve")

    after = one(approved.text, "[data-component=pending_change]")
    assert text(one(after, "[data-tone=ok]")) == "Approved"
    row = await get_user_memory(async_db_session, "u1")
    assert row is not None
    assert row.memory["structured_facts"][0]["fact"] == "house is worth $711,200"


@pytest.mark.asyncio
async def test_the_owners_own_edit_still_updates_directly(
    client: TestClient,
    async_db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the assistant's writes are cards: the owner correcting a fact
    through the API is the decision, and lands at once."""
    import app.core.db as db

    monkeypatch.setattr(db, "get_async_session", opens(async_db_session))
    await save_user_fact("u1", "works 9 to 5", "personal", session=async_db_session)
    await async_db_session.commit()

    response = client.patch(
        "/api/v1/ai/user-memory/0?user_id=u1", json={"fact": "works 8 to 4"}
    )

    assert response.status_code == 200
    row = await get_user_memory(async_db_session, "u1")
    assert row is not None
    assert row.memory["structured_facts"][0]["fact"] == "works 8 to 4"
