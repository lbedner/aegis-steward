"""A pasted wall of text is stored once, not replayed forever.

People paste pages - an order listing, a log, a spreadsheet dump - and
every one of them used to go into the message text, which is wrong twice
over. It buries the conversation on screen, and because history is
REPLAYED it rides every later turn until the budget pushes it out.
Measured on one real session: twelve pasted pages, 144,196 characters,
accounted for 1,422,367 characters of replayed history across 102 turns
- about ten times over - and still fell out of the window by the end.
"""

import pytest

from app.services.ai.domains.chat.pastes import (
    PASTE_THRESHOLD,
    lift,
    marker,
    paste_id,
    pasted,
    store_paste,
    title_of,
)
from app.services.ai.domains.chat.user_memory import memory_user

PAGE = "Your Orders\n" + ("Amazon order line. " * 200)


class TestWhatCountsAsAPaste:
    def test_a_question_is_left_alone(self) -> None:
        """The threshold is about size, but nothing under it is touched
        at all - a long question is still a question."""
        assert lift("what is my grocery budget?") == (
            "what is my grocery budget?",
            [],
        )

    def test_the_wall_is_lifted_and_the_sentence_around_it_stays(self) -> None:
        """A page almost never arrives alone: it comes with the line
        that introduces it, and lifting the whole message would take the
        question with it."""
        text, blocks = lift(f"here is another page\n\n{PAGE}")

        assert blocks == [PAGE]
        assert text.startswith("here is another page")
        assert PAGE not in text

    def test_two_walls_in_one_message_are_two_pastes(self) -> None:
        _, blocks = lift(f"{PAGE}\n\nand this one too\n\n{PAGE}")

        assert len(blocks) == 2

    def test_a_long_message_of_short_blocks_keeps_them(self) -> None:
        """Size decides whether to look; the BLOCK decides what to take."""
        chatty = "\n\n".join(["a paragraph someone typed."] * 200)
        assert len(chatty) > PASTE_THRESHOLD

        text, blocks = lift(chatty)

        assert blocks == []
        assert text == chatty


class TestTheMarker:
    def test_it_says_what_the_paste_is_and_how_to_read_it(self) -> None:
        """A marker the agent cannot act on is a hole in the
        conversation."""
        line = marker(
            {"id": "a3f19c2b", "title": "Your Orders", "chars": 16_681}
        )

        assert "#a3f19c2b" in line
        assert "Your Orders" in line
        assert "16,681 characters" in line
        assert 'pasted("a3f19c2b")' in line

    def test_the_id_is_short_enough_to_quote_back(self) -> None:
        """The storage key is 78 characters; asking a model to reproduce
        one exactly is asking for a hallucinated id."""
        key = "sha256/ab/cd/" + "f" * 64

        assert paste_id(key) == "ffffffff"

    def test_the_title_skips_navigation_furniture(self) -> None:
        assert title_of("All\nCart\nYour Orders for September\nx") == (
            "Your Orders for September"
        )


class TestStoringAndReadingBack:
    @pytest.mark.asyncio
    async def test_the_text_comes_back_exactly(self) -> None:
        with memory_user("paste-u1"):
            paste = await store_paste("paste-u1", PAGE, title="Your Orders")

            assert await pasted(paste["id"]) == PAGE

    @pytest.mark.asyncio
    async def test_the_same_page_pasted_twice_is_one_entry(self) -> None:
        """Content addressing makes the second paste free; an index row
        per paste of the same page would not be."""
        from app.services.ai.domains.chat.user_memory import load_user_pastes

        first = await store_paste("paste-u2", PAGE)
        again = await store_paste("paste-u2", PAGE)

        assert first["id"] == again["id"]
        assert len(await load_user_pastes("paste-u2")) == 1

    @pytest.mark.asyncio
    async def test_an_unknown_id_names_the_ones_on_file(self) -> None:
        """A refusal that lists what IS there is a correction the agent
        can act on; "not found" is one it will guess against."""
        with memory_user("paste-u3"):
            paste = await store_paste("paste-u3", PAGE)

            answer = await pasted("nosuchid")

        assert "nosuchid" in answer
        assert paste["id"] in answer

    @pytest.mark.asyncio
    async def test_a_paste_reaches_the_next_conversation(self) -> None:
        """Keyed to the user, like the readings and for the same reason:
        the page was pasted once and is still the page."""
        with memory_user("paste-u4", conversation_id="first"):
            paste = await store_paste("paste-u4", PAGE)

        with memory_user("paste-u4", conversation_id="second"):
            assert await pasted(paste["id"]) == PAGE


class TestTheAgentCanActuallyCallIt:
    def test_the_name_resolves_to_a_callable(self) -> None:
        from app.services.ai.domains.chat.tools import resolve_tools

        assert len(resolve_tools(["pasted"])) == 1

    def test_the_chat_agent_is_granted_it(self) -> None:
        from app.services.finance.domains.detection.analyst.seeds import (
            FINANCE_CHAT_TOOL_NAMES,
        )

        assert "pasted" in FINANCE_CHAT_TOOL_NAMES
