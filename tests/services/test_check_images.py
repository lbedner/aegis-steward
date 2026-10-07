"""A bank's check images are split out and attached to their checks (#415).

Chase's "check download" PDF puts a logo strip and then each check as a
row of two embedded scans (front, back) on every page; the reader took
the page's text layer - a copyright line - and saw nothing. Now each
check's front and back are pulled out of the PDF, read (OCR, then the
vision model only where OCR cannot pin the row or the row has no payee),
matched to its ledger row by check number and amount, and proposed as a
card: approve it and the scans are filed on that row, the payee with
them. Nothing is guessed: no match, no card.
"""

from __future__ import annotations

from datetime import date
import io
from typing import Any

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.documents.domains.reading import checks
from app.services.documents.service import DocumentService
from app.services.finance.constants import transaction_tag
from app.services.finance.domains.writes.queue import approve, list_changes
from app.services.finance.models import FinanceTransaction
from app.services.finance.service import FinanceService
from tests._session import opens
from tests.services._finance_factories import seed_account

# What the vision model returned for check 1802's front (2026-10-06, live).
VISION_1802 = """MR. LEONARD J. BEDNER
MRS. MARISA M. BEDNER
3 KELLEY CIR.
POUGHKEEPSIE, NY 12601

50-17/223
1802

DATE 6/30/25

PAY TO THE
ORDER OF Dr. Clark
$40.00

forty 00/100
DOLLARS

CHASE
JPMorgan Chase Bank, N.A.
www.Chase.com

MEMO

Marisa Bedner

021300173 957483639 1802"""

# What Tesseract read off check 1524's front (2026-10-06, live).
OCR_1524 = (
    "1524 Nx, Leonard J. Badnex 50-17/223 AAs, Marisa MA Bednar 3 Kalley "
    "Circle ; Poughke2psiz. Ny 12601 oat 2: | 25 __ moe Jolene 2 arte $25.00 . "
    "se ———— o° /D0_. DOLLARS a Ee CHASE spMorgan Chase Bank, N.A."
)
OCR_1802 = "MR, LEONARD J. BEDNER 50-17/223 POE NY on (3025 saat D) Choe $ 4.007. CHASE"


def _jpeg(width: int, height: int, color: str) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, "JPEG")
    return out.getvalue()


def _download(rows: list[tuple[str, str]]) -> bytes:
    """A check download as Chase lays one out: a logo strip, then a row
    per check - front at x=18, back at x=300."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument.new()
    page = pdf.new_page(612, 792)
    placed = [(1156, 107, "navy", 18, 756, 194, 18)]
    for n, (front, back) in enumerate(rows):
        y = 590 - n * 172
        placed += [
            (1800, 830, front, 18, y, 260, 120),
            (1800, 830, back, 300, y, 260, 120),
        ]
    for width, height, color, x, y, w, h in placed:
        image = pdfium.PdfImage.new(pdf)
        image.load_jpeg(io.BytesIO(_jpeg(width, height, color)), inline=False)
        image.set_matrix(pdfium.PdfMatrix().scale(w, h).translate(x, y))
        page.insert_obj(image)
    page.gen_content()
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


class TestReadingACheck:
    def test_the_vision_transcription_gives_every_field(self) -> None:
        read = checks.parse_check(VISION_1802)
        assert (read.number, read.amount_cents, read.payee, read.dated) == (
            "1802",
            4000,
            "Dr. Clark",
            date(2025, 6, 30),
        )
        assert read.account == "957483639"

    def test_ocr_gives_what_it_can_and_never_a_payee_it_cannot_see(self) -> None:
        read = checks.parse_check(OCR_1524)
        assert (read.number, read.amount_cents, read.payee) == ("1524", 2500, None)

    def test_a_check_is_known_by_its_markers(self) -> None:
        assert checks.looks_like_check(OCR_1524)
        assert checks.looks_like_check(OCR_1802)  # the routing fraction alone
        assert not checks.looks_like_check("Quarterly statement for the period")


def _row(number: str, cents: int, posted: date) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        id=hash((number, cents, posted)),
        number=number,
        amount=cents,
        date_=posted,
        merchant_id=None,
    )


class TestMatchingWhatWasRead:
    """The dry run on #21 and #22 (2026-10-06): 26 of 35 matched by
    number; the rest were an illegible number or a row renamed in
    Quicken, and a transcription wraps its fields in table bars."""

    def test_a_payee_is_read_without_the_bars_and_wide_spaces(self) -> None:
        read = checks.parse_check(
            "PAY TO THE\nORDER OF | Hyde Park Swim & Tennis Club |\n$178.00"
        )
        assert read.payee == "Hyde Park Swim & Tennis Club"
        smart = checks.parse_check("ORDER OF Smart Park\u00a0\u00a0\u00a0 $214.11")
        assert smart.payee == "Smart Park"

    def test_an_illegible_number_matches_the_one_check_row_of_its_amount(self) -> None:
        rows = [
            _row("1796", -2745, date(2025, 2, 3)),
            _row("1799", -3745, date(2025, 5, 27)),
        ]
        read = checks.CheckReading(amount_cents=2745)
        assert checks.match(read, rows).number == "1796"

    def test_a_read_number_never_takes_another_numbers_row(self) -> None:
        """#1526 for $38.93 is not CHECK # 1535 for $38.93."""
        rows = [_row("1535", -3893, date(2025, 12, 11))]
        read = checks.CheckReading(
            number="1526", amount_cents=3893, dated=date(2025, 12, 1)
        )
        assert checks.match(read, rows) is None

    def test_a_renamed_row_is_found_by_amount_and_the_date_it_cleared(self) -> None:
        """Michael Rossi's checks were renamed Portasoft and lost their
        numbers: only the amount, cleared after the date on its face."""
        renamed = [
            _row("", -3893, date(2025, 1, 9)),
            _row("", -3893, date(2025, 2, 5)),
        ]
        read = checks.CheckReading(
            number="1847", amount_cents=3893, dated=date(2025, 1, 3)
        )
        assert checks.match(read, [], renamed).date_ == date(2025, 1, 9)
        # No date read, nothing to tell the months apart: no answer.
        undated = checks.CheckReading(number="1526", amount_cents=3893)
        assert checks.match(undated, [], renamed) is None

    def test_a_misread_payee_is_snapped_to_yours(self) -> None:
        assert checks.known_payee("Nuance Health", ["NuVance Health", "Dr. Clark"]) == (
            "NuVance Health"
        )
        assert checks.known_payee("Holly Cow", ["NuVance Health"]) == "Holly Cow"


class TestSplittingTheDownload:
    def test_each_row_is_one_check_front_and_back_and_the_logo_is_dropped(
        self,
    ) -> None:
        found = checks.split_checks(_download([("white", "gray"), ("ivory", "silver")]))

        assert [(c.page, c.slot) for c in found] == [(1, 0), (1, 1)]
        assert all(c.front[:3] == b"\xff\xd8\xff" and c.back for c in found)

    def test_a_pdf_without_check_scans_has_none(self) -> None:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument.new()
        pdf.new_page(612, 792)
        out = io.BytesIO()
        pdf.save(out)
        assert checks.split_checks(out.getvalue()) == []


async def _rows(db: AsyncSession) -> dict[str, int]:
    svc = FinanceService(db)
    account = await seed_account(svc, name="TOTAL CHECKING")
    assert account.id is not None
    ids = {}
    for number, cents, day in (
        ("1524", -2500, date(2025, 7, 1)),
        ("1802", -4000, date(2025, 6, 30)),
    ):
        row = await svc.create_transaction(
            owner_user_id=1,
            account_id=account.id,
            amount=cents,
            txn_date=day,
            name=f"CHECK # {number} {number}",
            source="csv",
        )
        assert row.id is not None
        ids[number] = row.id
    # 1524 already has its payee: OCR's match is enough, no model call.
    named = await svc.create_merchant("Jolene Plant", owner_user_id=1)
    row = await db.get(FinanceTransaction, ids["1524"])
    assert row is not None
    row.merchant_id = named.id
    db.add(row)
    await db.flush()
    return ids


class TestProposingTheCards:
    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # a read, then a second read
    async def test_each_check_lands_on_its_row_with_the_payee_where_missing(
        self, async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ids = await _rows(async_db_session)
        document = await DocumentService(async_db_session).ingest(
            _download([("white", "gray"), ("ivory", "silver")]),
            title="checkdownload-3639.pdf",
            media_type="application/pdf",
        )
        assert document.id is not None
        by_front = {
            checks.split_checks(_download([("white", "gray"), ("ivory", "silver")]))[
                i
            ].front: text
            for i, text in enumerate((OCR_1524, ""))
        }
        monkeypatch.setattr(checks, "ocr", lambda image: by_front.get(image, ""))
        asked: list[bytes] = []

        async def _vision(image: bytes, _media_type: str) -> tuple[str, str]:
            asked.append(image)
            return VISION_1802, "vision"

        made = await checks.propose_checks(
            opens(async_db_session), document.id, owner_user_id=None, read=_vision
        )
        again = await checks.propose_checks(
            opens(async_db_session), document.id, owner_user_id=None, read=_vision
        )

        assert (made, again) == (2, 0)
        # Only 1802 - OCR read nothing off it, front or back, and its shape
        # (a front with its back) still sent it to the model.
        assert len(asked) == 1
        cards = {
            c.payload["transaction_id"]: c.payload
            for c in await list_changes(async_db_session, status="pending")
            if c.change_type == checks.CHECK
        }
        assert set(cards) == {ids["1524"], ids["1802"]}
        assert cards[ids["1802"]]["payee"] == "Dr. Clark"
        assert cards[ids["1524"]]["payee"] is None  # it has one

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # the rows seeded, then read back
    async def test_approving_files_front_and_back_on_the_row_and_names_it(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.core.storage import get_storage
        from app.services.finance.domains.writes.queue import propose

        ids = await _rows(async_db_session)
        store = get_storage()
        payload: dict[str, Any] = {
            "document_id": 1,
            "page": 2,
            "slot": 1,
            "transaction_id": ids["1802"],
            "number": "1802",
            "front_key": await store.put(
                _jpeg(1800, 830, "white"), content_type="image/jpeg"
            ),
            "back_key": await store.put(
                _jpeg(1800, 830, "gray"), content_type="image/jpeg"
            ),
            "payee": "Dr. Clark",
            "payee_because": "ORDER OF Dr. Clark",
        }
        card = await propose(
            async_db_session, checks.CHECK, payload, owner_user_id=None
        )

        await approve(async_db_session, card.id)

        filed, _ = await DocumentService(async_db_session).list_documents(
            tag=transaction_tag(ids["1802"])
        )
        assert sorted(d.title for d in filed) == ["Check 1802", "Check 1802 (back)"]
        row = await async_db_session.get(FinanceTransaction, ids["1802"])
        assert row is not None and row.merchant_id is not None
