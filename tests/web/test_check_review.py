"""A check card shows its own check, and its payee can be fixed (#420).

The preview was the whole PDF page - four checks - though the card holds
the one check's front scan; and the card could not be edited, so a
misread payee could only be approved wrong or rejected.
"""

from __future__ import annotations

import io

from fastapi.testclient import TestClient
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.documents.domains.reading import checks
from app.services.finance.domains.writes.queue import propose
from tests.web.conftest import Ledger
from tests.web.dom import none, one


def _jpeg() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (1800, 830), "white").save(out, "JPEG")
    return out.getvalue()


@pytest.fixture(params=["text", "cited"])
async def check_card(
    request: pytest.FixtureRequest, async_db_session: AsyncSession, ledger: Ledger
) -> int:
    """A pending check card, filed with the payee as text, or as a card
    filed before #420 was: the payee a cited reading."""
    from app.core.storage import get_storage
    from app.services.finance.service import FinanceService

    rows, _ = await FinanceService(async_db_session).list_transactions(
        owner_user_id=None, page_size=1
    )
    key = await get_storage().put(_jpeg(), content_type="image/jpeg")
    change = await propose(
        async_db_session,
        checks.CHECK,
        {
            "document_id": 1,
            "page": 2,
            "slot": 0,
            "transaction_id": rows[0].id,
            "number": "1801",
            "front_key": key,
            "back_key": None,
            "payee": "Holly Cow",
            "payee_because": "ORDER OF Holly Cow",
        },
        owner_user_id=None,
        proposed_by_agent="reading",
    )
    if request.param == "cited":
        change.payload = {
            **{k: v for k, v in change.payload.items() if k != "payee_because"},
            "payee": {"value": "Holly Cow", "page": 2, "because": "ORDER OF Holly Cow"},
        }
        async_db_session.add(change)
    await async_db_session.commit()
    assert change.id is not None
    return change.id


class TestTheCheckOnTheCard:
    def test_the_card_shows_this_checks_scan_not_the_page(
        self, client: TestClient, check_card: int
    ) -> None:
        card = one(client.get("/review").text, f"#change-{check_card}")
        scan = one(card, "img[data-scan]")
        assert scan.get("src") == f"/review/changes/{check_card}/scan"
        none(card, "img[data-page]")

    def test_the_scan_is_served(self, client: TestClient, check_card: int) -> None:
        served = client.get(f"/review/changes/{check_card}/scan")
        assert served.status_code == 200
        assert served.headers["content-type"] == "image/jpeg"
        assert served.content[:3] == b"\xff\xd8\xff"

    def test_a_card_without_a_scan_has_none_to_serve(
        self, client: TestClient, review: object
    ) -> None:
        assert client.get(f"/review/changes/{review.change}/scan").status_code == 404  # type: ignore[attr-defined]


class TestFixingThePayee:
    def test_the_edit_dialog_offers_the_payee_and_nothing_else(
        self, client: TestClient, check_card: int
    ) -> None:
        dialog = client.get(f"/review/changes/{check_card}/edit").text
        assert one(dialog, 'input[name="payee"]').get("value") == "Holly Cow"
        none(dialog, 'input[name="front_key"]')
        none(dialog, 'input[name="number"]')

    @pytest.mark.queryspy(threshold=3)  # the save, then the page
    def test_saving_it_changes_only_the_payee(
        self, client: TestClient, check_card: int
    ) -> None:
        client.post(f"/review/changes/{check_card}/edit", data={"payee": "Holy Cow"})
        card = one(client.get("/review").text, f"#change-{check_card}")
        assert "Holy Cow" in card.text_content()
        # The line it was read from stays, whichever shape it was filed in.
        assert "ORDER OF Holly Cow" in card.text_content()
        one(card, "img[data-scan]")


class TestAFiledPhotoOnItsCard:
    @pytest.mark.asyncio
    async def test_the_photo_is_drawn_and_served(
        self, client: TestClient, async_db_session: AsyncSession, ledger: Ledger
    ) -> None:
        """A photo filed from chat shows on its card, as a check does (#430)."""
        from app.core.storage import get_storage
        from app.services.ai.domains.chat.pastes import store_image
        from app.services.matters.matters import MatterService

        matter = await MatterService(async_db_session).open(title="2025 Tax Return")
        key = await get_storage().put(_jpeg(), content_type="image/jpeg")
        photo = await store_image(
            "0", key, "image/jpeg", "IMG_6611.jpeg", async_db_session
        )
        change = await propose(
            async_db_session,
            "document.file",
            {"paste_id": photo["id"], "matter_id": matter.id},
            owner_user_id=None,
        )
        await async_db_session.commit()

        card = one(client.get("/review").text, f"#change-{change.id}")
        assert (
            one(card, "img[data-scan]").get("src")
            == f"/review/changes/{change.id}/scan"
        )
        served = client.get(f"/review/changes/{change.id}/scan")
        assert served.status_code == 200 and served.content[:3] == b"\xff\xd8\xff"
