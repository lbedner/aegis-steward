"""Reading a letter's demands, and refusing to believe most of what a
model says about them.

A model is the only thing that can pull bullet points out of prose, so
this is where one earns its place. What it does NOT get is trust: every
item it returns has to name the page it read and quote the line, and
anything it hands back that the document cannot support is dropped
before a card is ever proposed. The model reads; it does not decide.
"""

from datetime import date

from app.services.documents.domains.reading.letters import (
    LetterReading,
    ReadItem,
    checked,
)

PAGES = [
    {"page": 1, "text": "Dear Mr. Bedner:\nYou must return the form by 9/8/2026."},
    {"page": 2, "text": "Proof of gross monthly income for each pension."},
]


GOOD = ReadItem(
    asked="Return the enclosed renewal form.",
    kind="form",
    page=1,
    quote="You must return the form by 9/8/2026.",
)


def _reading(**over: object) -> LetterReading:
    base: dict[str, object] = {
        "due_on": date(2026, 9, 8),
        "received_on": date(2026, 8, 24),
        "items": [GOOD],
    }
    return LetterReading(**(base | over))


class TestAnItemMustPointAtThePage:
    def test_a_page_the_document_does_not_have_is_dropped(self) -> None:
        """The cheapest hallucination to catch: a citation to page nine
        of a two-page letter."""
        reading = _reading(
            items=[
                GOOD,
                ReadItem(asked="Something", kind="document", page=9, quote="x"),
            ]
        )
        assert [i.asked for i in checked(reading, PAGES).items] == [GOOD.asked]

    def test_a_quote_that_is_not_on_that_page_is_dropped(self) -> None:
        """A quote is a promise the reader can check, so it gets checked."""
        reading = _reading(
            items=[
                GOOD,
                ReadItem(
                    asked="Send us a cheque.",
                    kind="action",
                    page=1,
                    quote="Remit payment of $400 immediately.",
                ),
            ]
        )
        assert [i.asked for i in checked(reading, PAGES).items] == [GOOD.asked]

    def test_an_item_that_quotes_its_page_is_kept(self) -> None:
        kept = checked(_reading(), PAGES).items
        assert [i.asked for i in kept] == ["Return the enclosed renewal form."]

    def test_whitespace_does_not_decide_whether_a_quote_matches(self) -> None:
        reading = _reading(
            items=[
                ReadItem(
                    asked="Return it.",
                    kind="form",
                    page=1,
                    quote="You  must return\nthe form by 9/8/2026.",
                )
            ]
        )
        assert len(checked(reading, PAGES).items) == 1


class TestWhatElseItRefuses:
    def test_an_ask_with_no_sentence_is_nothing(self) -> None:
        reading = _reading(
            items=[GOOD, ReadItem(asked="   ", kind="form", page=1, quote="You must")]
        )
        assert [i.asked for i in checked(reading, PAGES).items] == [GOOD.asked]

    def test_a_kind_nobody_defined_becomes_the_common_one(self) -> None:
        """The SENTENCE is the fact; the kind is a label a person can
        correct in one click. Dropping a real demand over its label would
        lose more than it protects."""
        reading = _reading(
            items=[
                ReadItem(
                    asked="Return the form.",
                    kind="paperwork",
                    page=1,
                    quote="You must return the form",
                )
            ]
        )
        assert checked(reading, PAGES).items[0].kind == "document"

    def test_a_kind_it_defined_is_left_alone(self) -> None:
        assert checked(_reading(), PAGES).items[0].kind == "form"

    def test_the_same_demand_twice_is_one_demand(self) -> None:
        item = ReadItem(
            asked="Return the enclosed renewal form.",
            kind="form",
            page=1,
            quote="You must return the form by 9/8/2026.",
        )
        assert len(checked(_reading(items=[item, item]), PAGES).items) == 1

    def test_a_deadline_before_the_letter_arrived_is_dropped(self) -> None:
        """A due date earlier than the day it was written is a misread,
        not a deadline that has already passed."""
        reading = _reading(due_on=date(2026, 8, 1), received_on=date(2026, 8, 24))
        assert checked(reading, PAGES).due_on is None
        assert checked(reading, PAGES).received_on == date(2026, 8, 24)


class TestSayingWhatWasLost:
    """A model copies a quote imperfectly about one demand in six, and
    that demand is dropped. Five asks where the letter made six is a
    card somebody trusts as complete, so the card has to say it is not."""

    def test_the_count_of_dropped_demands_is_carried(self) -> None:
        reading = _reading(
            items=[GOOD, ReadItem(asked="Ghost", kind="document", page=9, quote="x")]
        )
        assert checked(reading, PAGES).dropped == 1

    def test_a_clean_reading_drops_nothing(self) -> None:
        assert checked(_reading(), PAGES).dropped == 0

    def test_a_repeat_is_not_a_loss(self) -> None:
        """The same demand twice is one demand, not one demand and one
        casualty."""
        assert checked(_reading(items=[GOOD, GOOD]), PAGES).dropped == 0


class TestNothingWorthAsking:
    def test_a_letter_that_demands_nothing_reads_as_nothing(self) -> None:
        assert checked(_reading(items=[]), PAGES) is None

    def test_a_reading_whose_every_item_failed_reads_as_nothing(self) -> None:
        reading = _reading(
            items=[ReadItem(asked="Ghost", kind="document", page=7, quote="nowhere")]
        )
        assert checked(reading, PAGES) is None


class TestWhatTheModelIsTold:
    """The schema is the instruction the model actually reads. Two of
    these were learned the expensive way on a real letter: it returned
    "request" for every kind because nothing named the vocabulary, and
    it paraphrased a quote because nothing said the quote is checked."""

    def test_it_is_told_every_kind_it_may_use(self) -> None:
        from app.services.documents.domains.reading.letters import ReadItem
        from app.services.matters.changes import ITEM_KIND_KEYS

        told = ReadItem.model_json_schema()["properties"]["kind"]["description"]
        for kind in ITEM_KIND_KEYS:
            assert kind in told

    def test_it_is_told_the_quote_is_checked_against_the_page(self) -> None:
        from app.services.documents.domains.reading.letters import ReadItem

        told = ReadItem.model_json_schema()["properties"]["quote"]["description"]
        assert "exact" in told.casefold()
        assert "discard" in told.casefold()

    def test_every_field_says_what_it_is_for(self) -> None:
        from app.services.documents.domains.reading.letters import (
            LetterReading,
            ReadItem,
        )

        for model in (ReadItem, LetterReading):
            for name, spec in model.model_json_schema()["properties"].items():
                assert spec.get("description"), f"{model.__name__}.{name} says nothing"
