"""Telling Illiana what you approved.

She proposed a contact and said she would file the statement and link
the bank "once the organization proposal is approved". It was approved,
and she never heard: nothing sends an approval back to the conversation
that asked for it, so the next move needed a person to retype an id she
already had.

One message per approval ACTION, not per card, and only after the write
has landed - she reads it, calls parties(), and the row has to be there.
"""

import pytest

from app.services.finance.domains.writes.announce import announce_approval, said


class Row:
    """A resolved change, as the queue hands one back."""

    def __init__(self, **over: object) -> None:
        self.change_type = "contact.create"
        self.conversation_id = "conv-1"
        self.result = {
            "party_id": 8,
            "name": "JPMorgan Chase Bank, N.A.",
            "display": [{"label": "Contact", "value": "JPMorgan Chase Bank, N.A."}],
        }
        self.__dict__.update(over)


class TestWhatItSays:
    def test_it_names_the_card_and_the_ids_it_made(self) -> None:
        line = said(Row())
        assert "Add a person or an organization" in line
        assert "JPMorgan Chase Bank, N.A." in line
        assert "party_id 8" in line

    def test_the_display_is_what_it_quotes_not_the_payload(self) -> None:
        """The frozen display is what you SAW when you approved; the
        payload is ids. Quoting the payload would say "party 5" where
        the card said a name."""
        line = said(Row(result={"display": [{"label": "About", "value": "James"}]}))
        assert "James" in line

    def test_a_card_that_recorded_nothing_still_names_itself(self) -> None:
        assert "Add a person" in said(Row(result={}))


class TestWhoHearsAboutIt:
    @pytest.mark.asyncio
    async def test_one_message_per_conversation_not_per_card(self) -> None:
        sent: list[tuple[str, str]] = []

        async def fake(conversation_id: str, message: str) -> None:
            sent.append((conversation_id, message))

        await announce_approval(
            [Row(), Row(result={"party_id": 9, "display": []}), Row()], send=fake
        )

        assert len(sent) == 1
        assert sent[0][0] == "conv-1"
        bullets = [one for one in sent[0][1].splitlines() if one.startswith("- ")]
        assert len(bullets) == 3  # three cards, listed under one line

    @pytest.mark.asyncio
    async def test_two_conversations_each_hear_their_own(self) -> None:
        sent: list[tuple[str, str]] = []

        async def fake(conversation_id: str, message: str) -> None:
            sent.append((conversation_id, message))

        await announce_approval([Row(), Row(conversation_id="conv-2")], send=fake)

        assert sorted(one for one, _ in sent) == ["conv-1", "conv-2"]

    @pytest.mark.asyncio
    async def test_a_card_nobody_proposed_tells_nobody(self) -> None:
        """A payee renamed by hand in the register has no conversation.
        Approving it is not a reply to anything."""
        sent: list[tuple[str, str]] = []

        async def fake(conversation_id: str, message: str) -> None:
            sent.append((conversation_id, message))

        await announce_approval([Row(conversation_id=None)], send=fake)

        assert sent == []

    @pytest.mark.asyncio
    async def test_nothing_approved_says_nothing(self) -> None:
        sent: list[tuple[str, str]] = []

        async def fake(conversation_id: str, message: str) -> None:
            sent.append((conversation_id, message))

        await announce_approval([], send=fake)

        assert sent == []

    @pytest.mark.asyncio
    async def test_one_card_reads_as_a_sentence_not_a_list(self) -> None:
        sent: list[tuple[str, str]] = []

        async def fake(conversation_id: str, message: str) -> None:
            sent.append((conversation_id, message))

        await announce_approval([Row()], send=fake)

        assert "\n-" not in sent[0][1]
