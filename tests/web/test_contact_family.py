"""A contact's page says who they are related to (#289).

Seeded where the page reads, the database app code opens itself, and
removed after: it lives for the whole run.
"""

from collections.abc import AsyncIterator

from fastapi.testclient import TestClient
import pytest
from sqlmodel import col, delete

from app.core.db import get_async_session
from app.services.matters import relations
from app.services.matters.models import Party, PartyRelation
from app.services.matters.service import PartyService
from tests.web.dom import one, select, text


@pytest.fixture
async def family() -> AsyncIterator[tuple[int, int]]:
    async with get_async_session() as db:
        people = PartyService(db)
        parent = await people.create(name="Leonard Family-Test", kind="person")
        child = await people.create(name="Vanessa Family-Test", kind="person")
        await db.flush()
        ids = (int(parent.id or 0), int(child.id or 0))
        await relations.relate(db, *ids, "parent")
        await db.commit()
    yield ids
    async with get_async_session() as db:
        await db.exec(
            delete(PartyRelation).where(col(PartyRelation.party_id) == ids[0])
        )
        await db.exec(delete(Party).where(col(Party.id).in_(ids)))
        await db.commit()


@pytest.mark.queryspy(threshold=3)  # both contacts' pages, on purpose
def test_both_sides_say_it(client: TestClient, family: tuple[int, int]) -> None:
    parent, child = family

    on_parent = one(client.get(f"/contacts/{parent}").text, "#contact-family")
    on_child = one(client.get(f"/contacts/{child}").text, "#contact-family")

    assert [text(r) for r in select(on_parent, "[data-relation]")] == [
        "parent of Vanessa Family-Test"
    ]
    assert [text(r) for r in select(on_child, "[data-relation]")] == [
        "child of Leonard Family-Test"
    ]
