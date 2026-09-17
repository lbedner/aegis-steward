"""Policies on the insurer's page, and what covers a person.

Delta Dental's page showed an address and a note full of numbers. The
policy is a record: its numbers and terms drawn as rows, its claims as
a table with what is owed to providers totalled, and the two forms that
add to it - both on the page you are already looking at.
"""

from fastapi.testclient import TestClient
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.matters.words import WORDS
from tests._pdf import pdf_bytes
from tests.web.dom import none, one, select, text
from tests.web.test_contacts import _contact


def _policy(client: TestClient, insurer: int, covered: list[int]) -> int:
    answer = client.post(
        f"/contacts/{insurer}/policies/new",
        data={
            "kind": "dental",
            "name": "Delta Dental PPO Premium Plan",
            "member_id": "127396822501",
            "group_id": "18822-10007",
            "effective_on": "2026-08-01",
            "covered_sent": "1",
            "covered": [str(c) for c in covered],
            "terms": "Annual maximum: $2,000 per member\nDeductible: $50 per person",
        },
    )
    assert answer.status_code == 200, answer.text
    rows = select(answer.text, "[data-policy]")
    return int(rows[-1].get("data-policy"))


class TestThePoliciesCard:
    def test_the_insurer_page_loads_it_and_the_fragment_has_no_shell(
        self, client: TestClient, hx: TestClient
    ) -> None:
        insurer = _contact(client, "Card Dental", "organization")
        page = client.get(f"/contacts/{insurer}").text
        one(page, f'[hx-get="/contacts/{insurer}/policies"]')
        block = hx.get(f"/contacts/{insurer}/policies").text
        none(block, "html")
        one(block, f'[hx-get="/contacts/{insurer}/policies/new"]')
        assert text(one(block, "#policies [data-empty]"))

    def test_a_policy_is_drawn_with_its_numbers_and_terms(
        self, client: TestClient
    ) -> None:
        insurer = _contact(client, "Rows Dental", "organization")
        marisa = _contact(client, "Rows Marisa", "person")
        policy = _policy(client, insurer, [marisa])
        block = client.get(f"/contacts/{insurer}/policies").text
        card = one(block, f'[data-policy="{policy}"]')
        said = {
            text(one(row, "dt")): text(one(row, "dd")) for row in select(card, "dl div")
        }
        assert said["Member ID"] == "127396822501"
        assert said["Group ID"] == "18822-10007"
        assert said["Annual maximum"] == "$2,000 per member"
        assert one(card, f'a[data-contact="{marisa}"]') is not None
        assert text(one(card, "[data-owes]")) == "$0.00"

    def test_a_person_sees_what_covers_them(self, client: TestClient) -> None:
        insurer = _contact(client, "Covering Dental", "organization")
        leonard = _contact(client, "Covered Leonard", "person")
        policy = _policy(client, insurer, [leonard])
        page = client.get(f"/contacts/{leonard}").text
        one(page, f'[hx-get="/contacts/{leonard}/policies"]')
        block = client.get(f"/contacts/{leonard}/policies").text
        row = one(block, f'[data-covered-by="{policy}"]')
        assert one(row, f'a[href="/contacts/{insurer}"]') is not None
        none(block, f'[hx-get="/contacts/{leonard}/policies/new"]')

    def test_a_kind_nobody_defined_is_a_422_with_the_form_back(
        self, client: TestClient
    ) -> None:
        insurer = _contact(client, "Refusing Dental", "organization")
        answer = client.post(
            f"/contacts/{insurer}/policies/new",
            data={"kind": "pet", "name": "x", "covered_sent": "1"},
        )
        assert answer.status_code == 422
        one(answer.text, "[role=alert]")


class TestAClaimOnThePage:
    def test_the_eob_is_filed_with_the_insurer_and_the_total_adds_up(
        self, client: TestClient
    ) -> None:
        insurer = _contact(client, "Claims Dental", "organization")
        marisa = _contact(client, "Claims Marisa", "person")
        dentist = _contact(client, "Claims Dentist", "person")
        policy = _policy(client, insurer, [marisa])
        block = client.get(f"/contacts/{insurer}/policies").text
        one(block, f'[hx-get="/contacts/{insurer}/policies/{policy}/claims/new"]')

        answer = client.post(
            f"/contacts/{insurer}/policies/{policy}/claims/new",
            data={
                "covered_party_id": str(marisa),
                "service_on": "2026-08-12",
                "provider_party_id": str(dentist),
                "claim_number": "23548895152718",
                "status": "processed",
                "billed": "605.00",
                "allowed": "356.00",
                "insurer_paid": "210.00",
                "patient_owes": "146.00",
            },
            files={
                "file": (
                    "ClaimStatement.pdf",
                    pdf_bytes(["This is not a bill"]),
                    "application/pdf",
                )
            },
        )
        assert answer.status_code == 200, answer.text
        card = one(answer.text, f'[data-policy="{policy}"]')
        row = one(card, "tbody tr")
        cells = [text(td) for td in select(row, "td")]
        assert "$605.00" in cells and "$146.00" in cells
        assert one(row, f'a[data-contact="{dentist}"]') is not None
        door = one(row, "[data-open]")
        assert door.get("hx-get").startswith(f"/contacts/{insurer}/documents/")
        assert text(one(card, "[data-owes]")) == "$146.00"
        # And the EOB is on the insurer's paper.
        page = client.get(f"/contacts/{insurer}").text
        assert "ClaimStatement.pdf" in text(one(page, "#contact-paper"))

    def test_a_bad_amount_is_a_422(self, client: TestClient) -> None:
        insurer = _contact(client, "Bad Dental", "organization")
        marisa = _contact(client, "Bad Marisa", "person")
        policy = _policy(client, insurer, [marisa])
        answer = client.post(
            f"/contacts/{insurer}/policies/{policy}/claims/new",
            data={
                "covered_party_id": str(marisa),
                "service_on": "2026-08-12",
                "billed": "six hundred",
            },
        )
        assert answer.status_code == 422
        one(answer.text, "[role=alert]")


class TestIllianaSeesPolicyIds:
    @pytest.mark.asyncio
    async def test_the_parties_tool_hands_back_policy_ids(
        self, async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from contextlib import asynccontextmanager

        from app.services.insurance.service import InsuranceService
        from app.services.matters import ai_tools
        from app.services.matters.service import PartyService

        @asynccontextmanager
        async def test_session():
            yield async_db_session

        monkeypatch.setattr(ai_tools, "get_async_session", test_session)
        insurer = await PartyService(async_db_session).create(
            name="Tool Dental", kind="organization"
        )
        person = await PartyService(async_db_session).create(
            name="Tool Person", kind="person"
        )
        policy = await InsuranceService(async_db_session).create_policy(
            insurer_party_id=int(insurer.id),
            covered_party_ids=[int(person.id)],
            kind="dental",
        )
        await async_db_session.commit()
        told = {p["id"]: p for p in (await ai_tools.parties())["parties"]}
        assert told[insurer.id]["policy_ids"] == [policy.id]
        assert told[person.id]["covered_by_policy_ids"] == [policy.id]
        assert WORDS["policies"]
