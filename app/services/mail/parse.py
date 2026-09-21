"""Bytes to messages. Pure: no database, no network, nothing stored.

Both shapes Gmail hands out are stdlib: Google Takeout writes an
``.mbox``, "Download message" saves an ``.eml``, and ``mailbox`` and
``email`` read them. Everything downstream needs is on the headers and
the parts, so this is the whole reader.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
import hashlib
from html.parser import HTMLParser
import mailbox
from pathlib import Path
import re
import tempfile

MAIL_SUFFIXES = (".mbox", ".eml")


class NotMailError(ValueError):
    """The file is not a mailbox export or a saved message."""


@dataclass(frozen=True)
class Attachment:
    filename: str
    media_type: str | None
    data: bytes


@dataclass(frozen=True)
class ParsedMessage:
    """One message, as the pipeline reads it.

    ``message_id`` is the identity - RFC 5322 makes it unique per
    message, so a re-export of an overlapping range dedupes on it and
    nothing else. ``from_address`` is lower-cased once, here: it is
    matched against contacts, never shown back with somebody else's
    capitalisation.
    """

    message_id: str
    from_address: str
    from_name: str
    subject: str
    sent_at: datetime | None
    # The text part, or the HTML part flattened to text. No rendering, no
    # remote content: a document that phones home when opened is a
    # tracking pixel with a filename (MI-05).
    body_text: str = ""
    attachments: list[Attachment] = field(default_factory=list)


def is_mail_export(filename: str | None) -> bool:
    return bool(filename) and str(filename).lower().endswith(MAIL_SUFFIXES)


def messages(data: bytes, filename: str) -> list[ParsedMessage]:
    """Every message in the file, in file order."""
    if not is_mail_export(filename):
        raise NotMailError(
            f"{filename!r} is not a mailbox export (.mbox) or a saved message (.eml)."
        )
    if filename.lower().endswith(".eml"):
        return [_read(message_from_bytes(data, policy=policy.default))]
    # mailbox.mbox reads a path, not bytes: it seeks. A temp file is the
    # honest way in, and it is gone before this returns.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "export.mbox"
        path.write_bytes(data)
        box = mailbox.mbox(
            path, factory=lambda f: message_from_bytes(f.read(), policy=policy.default)
        )
        try:
            return [_read(one) for one in box]
        finally:
            box.close()


def _read(mail: EmailMessage) -> ParsedMessage:
    name, address = parseaddr(str(mail.get("From") or ""))
    return ParsedMessage(
        message_id=_identity(mail),
        from_address=address.lower(),
        from_name=name.strip(),
        subject=str(mail.get("Subject") or "").strip(),
        sent_at=_sent(mail),
        body_text=_body(mail),
        attachments=[
            Attachment(
                filename=part.get_filename() or "attachment",
                media_type=part.get_content_type(),
                data=part.get_payload(decode=True) or b"",
            )
            for part in mail.iter_attachments()
            if _is_paper(part)
        ],
    )


def _is_paper(part: EmailMessage) -> bool:
    """A part the sender ATTACHED, as opposed to one the body embeds.

    An HTML email carries its logos as ``inline`` parts with a
    ``Content-ID`` the body references by ``cid:``. When the whole
    message is ``multipart/related`` - which is how Optum's HSA notice
    arrived - stdlib's ``iter_attachments()`` hands those straight back,
    and the shelf filed a 12 KB logo and a 1 KB banner as documents and
    spent a model call reading each (2026-09-20). Inline plus a
    Content-ID is chrome; everything else is paper.
    """
    return not (part.get_content_disposition() == "inline" and part.get("Content-ID"))


def _identity(mail: EmailMessage) -> str:
    """The Message-ID, or - where a sender omitted one - a hash of the
    headers that make a message what it is. Derived, not minted: two
    imports of the same file have to agree it is one message."""
    given = str(mail.get("Message-ID") or "").strip()
    if given:
        return given
    stamp = "\n".join(str(mail.get(h) or "") for h in ("From", "To", "Date", "Subject"))
    return f"<sha256:{hashlib.sha256(stamp.encode()).hexdigest()}>"


def _sent(mail: EmailMessage) -> datetime | None:
    raw = mail.get("Date")
    if not raw:
        return None
    try:
        when = parsedate_to_datetime(str(raw))
    except (TypeError, ValueError):
        return None
    # One clock. A naive date is taken as UTC rather than guessed at.
    return when.astimezone(UTC) if when.tzinfo else when.replace(tzinfo=UTC)


def _body(mail: EmailMessage) -> str:
    """The text part as written; failing that, the HTML part as words."""
    part = mail.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    content = str(part.get_content())
    if part.get_content_type() == "text/html":
        return _flatten(content)
    return content.strip()


# Tags that end a line when flattened. A phone or an address on its own
# line is what the identity rules read; a <p> is a line.
_BLOCKS = frozenset(
    {"p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4", "td", "table", "blockquote"}
)


class _Words(HTMLParser):
    """HTML to the words a person would read off it. Styles and scripts
    are not words; block tags are line breaks."""

    def __init__(self) -> None:
        super().__init__()
        self.out: list[str] = []
        self.quiet = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("style", "script", "head"):
            self.quiet += 1
        if tag in _BLOCKS:
            self.out.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("style", "script", "head"):
            self.quiet = max(0, self.quiet - 1)
        if tag in _BLOCKS:
            self.out.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.quiet:
            self.out.append(data)


def _flatten(html: str) -> str:
    words = _Words()
    words.feed(html)
    text = re.sub(r"[ \t]+", " ", "".join(words.out))
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)
