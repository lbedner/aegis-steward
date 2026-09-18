"""The reach details a contact has that nothing has a field for.

Chase prints three phone numbers: the one you ring, the Spanish line and
the one from abroad. A contact has ONE phone field, so Illiana put the
other two in the note, where nobody can read them as numbers. Extra
reach details are a bag - the column has always been JSON for exactly
this reason - so they are kept as labelled lines beside the typed four.

The rule that decides which: if the app will ever do arithmetic on it,
compare it or sort by it, it needs a column. If a person only reads it,
a labelled line is enough. A second phone number is only ever read.
"""

from fastapi.testclient import TestClient
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.matters.reach import reach_lines
from tests.web.dom import none, one, select, text
from tests.web.test_contacts import _contact


class TestTheShape:
    def test_lines_are_label_and_value_pairs(self) -> None:
        assert reach_lines(
            {"phone": "1-800", "also": [{"label": "Español", "value": "1-877"}]}
        ) == [("Español", "1-877")]

    def test_a_contact_with_none_has_none(self) -> None:
        assert reach_lines({"phone": "1-800"}) == []
        assert reach_lines(None) == []

    def test_a_line_missing_either_half_is_not_a_line(self) -> None:
        """A label with nothing beside it says nothing, and a value
        nobody labelled is a number whose meaning is lost."""
        assert reach_lines(
            {
                "also": [
                    {"label": "Español", "value": ""},
                    {"label": "", "value": "1-877"},
                    {"label": "  ", "value": "  "},
                    {"label": "From abroad", "value": "1-713"},
                ]
            }
        ) == [("From abroad", "1-713")]


class TestOnTheForm:
    def test_they_save_and_come_back_for_editing(self, client: TestClient) -> None:
        party_id = _contact(client, "Lines Bank", "organization")

        saved = client.post(
            f"/contacts/{party_id}",
            data={
                "name": "Lines Bank",
                "kind": "organization",
                "sort_name": "",
                "note": "",
                "phone": "1-800-935-9935",
                "line_label": ["Español", "From abroad", ""],
                "line_value": ["1-877-312-4273", "1-713-262-1679", "ignored"],
            },
        )
        assert saved.status_code == 200

        form = client.get(f"/contacts/{party_id}/edit").text
        labels = [el.get("value") for el in select(form, 'input[name="line_label"]')]
        values = [el.get("value") for el in select(form, 'input[name="line_value"]')]
        assert "Español" in labels and "1-713-262-1679" in values
        # A blank row is a row somebody left alone, not a line.
        assert "ignored" not in values
        # And the one the app asks questions of is still its own field.
        assert one(form, 'input[name="phone"]').get("value") == "1-800-935-9935"

    def test_emptying_a_line_removes_it(self, client: TestClient) -> None:
        party_id = _contact(client, "Emptied Bank", "organization")
        said = {
            "name": "Emptied Bank",
            "kind": "organization",
            "sort_name": "",
            "note": "",
        }
        client.post(
            f"/contacts/{party_id}",
            data={**said, "line_label": ["Fax"], "line_value": ["1-555"]},
        )

        client.post(
            f"/contacts/{party_id}",
            data={**said, "line_label": ["Fax"], "line_value": [""]},
        )

        form = client.get(f"/contacts/{party_id}/edit").text
        assert "1-555" not in form


class TestOnThePage:
    def test_they_read_beside_the_typed_ones(self, client: TestClient) -> None:
        party_id = _contact(client, "Reading Bank", "organization")
        client.post(
            f"/contacts/{party_id}",
            data={
                "name": "Reading Bank",
                "kind": "organization",
                "sort_name": "",
                "note": "",
                "phone": "1-800-935-9935",
                "line_label": ["Español"],
                "line_value": ["1-877-312-4273"],
            },
        )

        page = client.get(f"/contacts/{party_id}").text
        reach = one(page, "[data-reach]")
        assert "Español" in text(reach)
        assert "1-877-312-4273" in text(reach)

    def test_a_contact_with_no_extras_draws_none(self, client: TestClient) -> None:
        party_id = _contact(client, "Plain Bank", "organization")
        none(client.get(f"/contacts/{party_id}").text, "[data-line]")


class TestWhatIllianaCanPropose:
    @pytest.mark.asyncio
    async def test_contact_create_takes_them(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.contacts import (
            CreateContactPayload,
            create_contact_describe,
            create_contact_execute,
        )
        from app.services.matters.service import PartyService

        payload = CreateContactPayload(
            name="JPMorgan Chase Bank, N.A.",
            kind="organization",
            phone="1-800-935-9935",
            also=[
                {"label": "Español", "value": "1-877-312-4273"},
                {"label": "From abroad", "value": "1-713-262-1679"},
            ],
        )
        said = {
            r.label: r.value
            for r in await create_contact_describe(async_db_session, payload, None)
        }
        assert said["Español"] == "1-877-312-4273"

        made = await create_contact_execute(async_db_session, payload, None)
        await async_db_session.commit()

        party = await PartyService(async_db_session).get(made["party_id"])
        assert reach_lines(party.contact) == [
            ("Español", "1-877-312-4273"),
            ("From abroad", "1-713-262-1679"),
        ]
