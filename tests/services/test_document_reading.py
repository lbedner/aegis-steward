"""Reading a document's own facts off its pages, deterministically.

ST-08's principle: extraction PROPOSES. Nothing read becomes true
without a card somebody approved. So the reader's job is not to be
clever, it is to be checkable: every finding names the page and the
phrase it came from, and a page that says nothing certain yields
nothing at all. A blank field is a prompt to look; a confidently wrong
execution date on a legal document is the failure the queue exists to
prevent.
"""

from datetime import date

from app.services.documents.domains.reading import read_document

NYSLRS = """IN002L 09/2024
As of 09/16/2026
This document serves as proof of income from the New York State and Local
Retirement System (NYSLRS).
Recipient's Name: James Bedner
Monthly Pension Benefit:
Base Benefit: $1089.47
"""

MORTGAGE = """CITIZENS BANK
Mortgage Interest Statement
Statement Date: March 3, 2026
Account Number: ****4419
"""

LETTER = """DUTCHESS COUNTY DEPARTMENT OF COMMUNITY & FAMILY SERVICES
August 24, 2026

Dear Mr. Bedner:

You must return the enclosed renewal form by September 8, 2026.
"""


def _pages(*texts: str) -> list[dict[str, object]]:
    return [{"page": n, "text": t} for n, t in enumerate(texts, start=1)]


class TestTheDateItCovers:
    def test_a_labelled_date_is_read_with_the_phrase_it_came_from(self) -> None:
        found = {f.field: f for f in read_document(_pages(NYSLRS))}
        assert found["document_date"].value == date(2026, 9, 16)
        assert found["document_date"].page == 1
        assert "As of 09/16/2026" in found["document_date"].because

    def test_a_written_month_is_a_date_too(self) -> None:
        found = {f.field: f for f in read_document(_pages(MORTGAGE))}
        assert found["document_date"].value == date(2026, 3, 3)
        assert "Statement Date" in found["document_date"].because

    def test_an_unlabelled_date_is_not_taken(self) -> None:
        """A number that looks like a date is not a claim about the
        document. Only a date its own page LABELS is."""
        loose = _pages("Paid 03/14/2026 to Acme for services rendered.")
        assert [f.field for f in read_document(loose)] == []

    def test_a_date_alone_on_its_line_is_the_dateline(self) -> None:
        """A letter's dateline stands by itself. The deadline further
        down sits inside a sentence, which makes it a different fact and
        not this document's own date."""
        found = {f.field: f for f in read_document(_pages(LETTER))}
        assert found["document_date"].value == date(2026, 8, 24)

    def test_a_date_nobody_could_mean_is_refused(self) -> None:
        assert [
            f
            for f in read_document(_pages("As of 13/45/2026"))
            if f.field == "document_date"
        ] == []


class TestWhatKindOfPaperItIs:
    def test_a_statement_says_so(self) -> None:
        found = {f.field: f for f in read_document(_pages(MORTGAGE))}
        assert found["kind"].value == "statement"
        assert "Mortgage Interest Statement" in found["kind"].because

    def test_a_letter_is_known_by_its_salutation(self) -> None:
        found = {f.field: f for f in read_document(_pages(LETTER))}
        assert found["kind"].value == "letter"

    def test_a_kind_it_cannot_tell_is_left_alone(self) -> None:
        """ "other" is never proposed: it is the default already, and a
        card that changes nothing is a card that wastes a decision."""
        assert [
            f for f in read_document(_pages("Page 1 of 1")) if f.field == "kind"
        ] == []

    def test_a_heading_says_the_kind_and_a_sentence_only_mentions_it(
        self,
    ) -> None:
        """Live: a Delta Dental claim statement was filed as a statement
        on the strength of "Did you know this statement is available
        electronically" - marketing prose, not a heading. The reading was
        right by luck, and a citation that proves nothing is the failure
        this whole surface exists to avoid (2026-09-19).
        """
        prose = "Did you know this statement is available electronically"
        assert [f for f in read_document(_pages(prose)) if f.field == "kind"] == []

        heading = "Claim Statement"
        found = {f.field: f for f in read_document(_pages(heading))}
        assert found["kind"].value == "statement"

    def test_the_papers_that_name_themselves(self) -> None:
        """Three of the four unnamed documents on the real shelf say what
        they are in their first line. They were unread because the
        vocabulary knew two kinds out of seven, not because the paper was
        silent."""
        for heading, kind in (
            ("Application", "form"),
            ("Combined Contract and Disclosure Form", "form"),
            ("Welcome to Delta Dental", "letter"),
            ("Explanation of Benefits", "statement"),
        ):
            found = {f.field: f for f in read_document(_pages(heading))}
            assert found["kind"].value == kind, heading
            assert found["kind"].because == heading

    def test_every_kind_read_is_one_the_shelf_allows(self) -> None:
        from app.services.documents.models import DOCUMENT_KINDS

        for text in (NYSLRS, MORTGAGE, LETTER, "Application", "Welcome to Us"):
            for finding in read_document(_pages(text)):
                if finding.field == "kind":
                    assert finding.value in DOCUMENT_KINDS


class TestWhereItLooks:
    def test_only_the_opening_pages_are_read(self) -> None:
        """A dateline and a letterhead are at the front. Page nine of a
        policy saying "Statement Date" is boilerplate, not this
        document's own date."""
        pages = _pages("Nothing here.", "Nor here.", "Nor here either.", MORTGAGE)
        assert read_document(pages) == []

    def test_an_unread_page_contributes_nothing(self) -> None:
        assert read_document([{"page": 1, "text": None}]) == []
        assert read_document([]) == []
