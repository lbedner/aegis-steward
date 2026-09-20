"""Reading a mailbox export: what a message is, once it is not on Google.

Pure - bytes in, messages out, no database. Everything the pipeline
downstream needs comes off the headers and the parts, and the stdlib
already knows how to read both an mbox and an .eml.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.services.mail import parse
from tests._mbox import PDF, eml_bytes, mbox_bytes, message


class TestWhatAFileIs:
    def test_takeout_and_a_single_message_are_both_mail(self) -> None:
        assert parse.is_mail_export("All mail Including Spam and Trash.mbox")
        assert parse.is_mail_export("Your claim.eml")
        assert not parse.is_mail_export("statement.pdf")
        assert not parse.is_mail_export("export.csv")

    def test_an_mbox_yields_every_message(self) -> None:
        data = mbox_bytes([message(subject="one"), message(subject="two")])
        read = parse.messages(data, "export.mbox")
        assert [m.subject for m in read] == ["one", "two"]

    def test_an_eml_yields_the_one_message(self) -> None:
        read = parse.messages(eml_bytes(message(subject="just this")), "x.eml")
        assert [m.subject for m in read] == ["just this"]


class TestWhatAMessageCarries:
    def test_the_sender_is_split_into_who_and_where(self) -> None:
        [read] = parse.messages(
            mbox_bytes([message(sender="Delta Dental <Claims@DeltaDentalIns.com>")]),
            "x.mbox",
        )
        assert read.from_name == "Delta Dental"
        # Lower-cased at the edge, once: an address is matched, never shown
        # back with somebody else's capitalisation.
        assert read.from_address == "claims@deltadentalins.com"

    def test_the_message_id_is_the_identity(self) -> None:
        """RFC 5322 makes it unique per message; a re-export of an
        overlapping range must dedupe on it, not on subject or date."""
        [read] = parse.messages(
            mbox_bytes([message(message_id="<abc123@mail.gmail.com>")]), "x.mbox"
        )
        assert read.message_id == "<abc123@mail.gmail.com>"

    def test_a_message_with_no_id_gets_a_stable_one(self) -> None:
        """Some senders omit it. Two imports of the same file must still
        agree it is one message, so the fallback is derived from the
        bytes, not minted fresh each time."""
        one = message()
        del one["Message-ID"]
        first = parse.messages(eml_bytes(one), "x.eml")[0].message_id
        again = parse.messages(eml_bytes(one), "x.eml")[0].message_id
        assert first == again and first

    def test_the_date_is_when_it_was_sent_in_utc(self) -> None:
        sent = datetime(2026, 8, 27, 9, 5, tzinfo=UTC)
        [read] = parse.messages(mbox_bytes([message(sent=sent)]), "x.mbox")
        assert read.sent_at == sent

    def test_attachments_come_off_as_files(self) -> None:
        [read] = parse.messages(
            mbox_bytes(
                [
                    message(
                        attachments=[
                            ("statement.pdf", "application/pdf", PDF),
                            ("eob.png", "image/png", b"\x89PNG\r\n\x1a\n"),
                        ]
                    )
                ]
            ),
            "x.mbox",
        )
        assert [(a.filename, a.media_type) for a in read.attachments] == [
            ("statement.pdf", "application/pdf"),
            ("eob.png", "image/png"),
        ]
        assert read.attachments[0].data == PDF

    def test_the_body_is_not_an_attachment(self) -> None:
        [read] = parse.messages(mbox_bytes([message(body="hello")]), "x.mbox")
        assert read.attachments == []

    def test_a_file_that_is_not_mail_is_refused_by_name(self) -> None:
        with pytest.raises(parse.NotMailError):
            parse.messages(b"%PDF-1.4", "statement.pdf")


class TestWhatIsNotPaper:
    """The first real email through the door was Optum's "Your HSA
    Statement is Now Available". The statement was not in it - it is a
    link to log in - and the two files the reader filed were the Optum
    logo and an orange banner, 12 KB and 1 KB of email chrome, each of
    which the shelf then spent a model call reading (2026-09-20)."""

    def test_images_embedded_in_the_body_are_not_attachments(self) -> None:
        chrome = message(
            inline_images=[("logo.png", b"\x89PNG"), ("banner.png", b"\x89PNG")]
        )
        [read] = parse.messages(eml_bytes(chrome), "x.eml")
        assert read.attachments == []

    def test_a_real_attachment_beside_embedded_images_is_still_paper(self) -> None:
        both = message(
            inline_images=[("logo.png", b"\x89PNG")],
            attachments=[("statement.pdf", "application/pdf", PDF)],
        )
        [read] = parse.messages(eml_bytes(both), "x.eml")
        assert [a.filename for a in read.attachments] == ["statement.pdf"]


class TestTheBodyIsTheLetter:
    """Half of what an agency sends has no attachment: the county emails
    the renewal reminder in the body, the insurer emails the EOB as text.
    That mail IS the letter (MI-05). No HTML rendering, no remote content:
    the text part is the letter, and an HTML-only mail is flattened."""

    def test_a_plain_body_is_kept_as_written(self) -> None:
        [read] = parse.messages(
            eml_bytes(message(body="Your claim was paid.")), "x.eml"
        )
        assert read.body_text == "Your claim was paid."

    def test_an_html_only_body_is_flattened_to_text(self) -> None:
        [read] = parse.messages(
            eml_bytes(
                message(body="Dear Leonard", inline_images=[("logo.png", b"\x89PNG")])
            ),
            "x.eml",
        )
        assert "Dear Leonard" in read.body_text
        assert "<" not in read.body_text and "cid:" not in read.body_text

    def test_styles_and_scripts_are_not_words(self) -> None:
        mail = message()
        mail.set_content(
            "<html><head><style>.x{color:red}</style></head>"
            "<body><script>track()</script><p>Sign in at optumfinancial.com</p>"
            "<p>844-288-6246</p></body></html>",
            subtype="html",
        )
        [read] = parse.messages(eml_bytes(mail), "x.eml")
        assert "color:red" not in read.body_text and "track()" not in read.body_text
        assert "Sign in at optumfinancial.com" in read.body_text
        # Block tags become line breaks, so a phone stays on its own line
        # for the identity rules, which read a page line by line.
        assert "844-288-6246" in read.body_text.splitlines()
