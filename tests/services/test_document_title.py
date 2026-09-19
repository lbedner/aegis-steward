"""Paper arrives named after a file, and a filename is not a name.

"20260826-statements-3639-.pdf" is what the bank called the download. It
tells a reader nothing, it cannot be asked for out loud, and it is why a
conversation about a document falls back to its id - which is a thing
nobody knows or should have to (2026-09-18).

No model. A title LOOKS like prose, which is what made reaching for one
tempting, but every part of it is already read: the kind and the date
come off the front page by pattern, and the organization comes from the
address book. Joining them is exact, free, and checkable by the person
approving it.
"""

from typing import Any

from app.services.documents.domains.reading.titles import (
    compose,
    looks_like_a_filename,
    whose_letterhead,
)

PAGES: list[Any] = [
    {
        "page": 1,
        "text": (
            "HUDSON VALLEY CREDIT UNION\n"
            "Statement period 08/01/2026 - 08/26/2026\n"
            "CLASSIC CHECKING ending 3639\n"
        ),
    },
    {"page": 2, "text": "Delta Dental of New York, Inc."},
]

ON_FILE = (
    "Hudson Valley Credit Union",
    "Delta Dental of New York, Inc.",
    "Hudson Valley",
)


class TestWhatCountsAsAName:
    def test_a_download_is_not_a_name(self) -> None:
        for filename in (
            "20260826-statements-3639-.pdf",
            "AP_DRTNY107_23548895152718_44328175.pdf",
            "scan0001.PDF",
            "IMG_4821.jpeg",
            "ppo_policy_document.pdf",
            "enrollee-notices-flyer.pdf",
        ):
            assert looks_like_a_filename(filename)

    def test_something_somebody_typed_is_left_alone(self) -> None:
        """A title a person already gave it is not ours to improve - and
        the test is the STEM, so a good name carrying ".pdf" is a good
        name."""
        for named in (
            "HVCU statement, August 2026",
            "Medicaid renewal request",
            "Dad's POA",
            "NYSLRS Monthly Statement.pdf",
            "Executed POA for Bedner.pdf",
        ):
            assert not looks_like_a_filename(named)


class TestWhoseLetterheadItIs:
    def test_the_organization_on_the_front(self) -> None:
        found = whose_letterhead(PAGES, ON_FILE)
        assert found is not None
        assert found.value == "Hudson Valley Credit Union"
        assert found.page == 1
        # Cited: the line it was read off, so the card can show it.
        assert found.because == "HUDSON VALLEY CREDIT UNION"

    def test_the_longest_name_wins(self) -> None:
        """"Hudson Valley" is also on file, and a shelf of documents from
        "Hudson Valley" is a shelf that lost the credit union."""
        assert whose_letterhead(PAGES, ON_FILE).value == "Hudson Valley Credit Union"

    def test_a_stranger_is_not_guessed_at(self) -> None:
        """Only organizations already on file. A letterhead read off the
        page and taken as a name is how "state.ny.us" became a website."""
        assert whose_letterhead(PAGES, ("Chase",)) is None

    def test_nobody_on_the_front_is_nobody(self) -> None:
        assert whose_letterhead([{"page": 1, "text": "Page 1 of 6"}], ON_FILE) is None


class TestTheNameItself:
    def test_who_what_and_when(self) -> None:
        found = compose(whose_letterhead(PAGES, ON_FILE), "statement", "2026-08-26")
        assert found.value == "Hudson Valley Credit Union statement, August 2026"

    def test_what_is_not_known_is_left_out(self) -> None:
        head = whose_letterhead(PAGES, ON_FILE)
        assert (
            compose(head, "statement", None).value
            == "Hudson Valley Credit Union statement"
        )
        assert (
            compose(head, None, "2026-08-26").value
            == "Hudson Valley Credit Union, August 2026"
        )

    def test_the_papers_own_heading_tells_two_of_a_kind_apart(self) -> None:
        """Two Delta Dental documents both came out "Delta Dental of New
        York, Inc. form", which separates them no better than their
        filenames did. The heading the kind was read FROM is the
        distinguishing thing, and it is already in hand."""
        from app.services.documents.domains.reading.findings import Finding

        head = whose_letterhead(PAGES, ON_FILE)
        application = Finding("kind", "form", 1, "Application")
        contract = Finding("kind", "form", 1, "Combined Contract and Disclosure Form")
        assert compose(head, application, None).value == (
            "Hudson Valley Credit Union Application"
        )
        assert compose(head, contract, None).value == (
            "Hudson Valley Credit Union Combined Contract and Disclosure Form"
        )

    def test_a_heading_that_already_names_the_sender_stands_alone(self) -> None:
        """"Hudson Valley Credit Union Welcome to Hudson Valley Credit
        Union" says it twice."""
        from app.services.documents.domains.reading.findings import Finding

        welcome = Finding("kind", "letter", 1, "Welcome to Hudson Valley Credit Union")
        said = compose(whose_letterhead(PAGES, ON_FILE), welcome, None)
        assert said.value == "Welcome to Hudson Valley Credit Union"

    def test_a_heading_that_is_really_a_sentence_falls_back_to_the_kind(
        self,
    ) -> None:
        from app.services.documents.domains.reading.findings import Finding

        wordy = Finding(
            "kind", "statement", 1, "Your statement of benefits for the plan year 2026"
        )
        said = compose(whose_letterhead(PAGES, ON_FILE), wordy, None)
        assert said.value == "Hudson Valley Credit Union statement"

    def test_an_organization_alone_is_not_a_name(self) -> None:
        """Run over a real shelf, this named four different documents
        "Delta Dental of New York, Inc.". Four papers with one name is
        the filename problem in tidier clothes, so the ones it cannot
        tell apart keep the filename and stay findable."""
        assert compose(whose_letterhead(PAGES, ON_FILE), None, None) is None
        assert compose(whose_letterhead(PAGES, ON_FILE), "other", None) is None

    def test_without_an_organization_there_is_no_name(self) -> None:
        """"statement, August 2026" is a category, not a name - and a
        shelf of those is the filename problem in tidier clothes."""
        assert compose(None, "statement", "2026-08-26") is None

    def test_a_name_too_long_to_read_is_refused(self) -> None:
        from app.services.documents.domains.reading.findings import Finding

        long = Finding("sender", "x" * 90, 1, "x" * 90)
        assert compose(long, "statement", "2026-08-26") is None
