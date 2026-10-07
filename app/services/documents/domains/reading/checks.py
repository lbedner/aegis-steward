"""A bank's check images, split out and attached to their checks (#415).

Chase's "check download" PDF is a cover page, then a logo strip and a
row of two embedded scans per check - front left, back right - on every
page. The reader took each page's text layer, which on those pages is a
copyright line, and saw nothing; Illiana read the scans by eye in chat
and none of it was kept (2026-10-06, documents #21 and #22).

So the scans are pulled out of the PDF as they are (``split_checks``),
each front is read - Tesseract first, the vision model only where OCR
cannot pin the ledger row or the row has no payee to hand back - and
matched to its row by check number and amount. Measured on #22: OCR read
"1524 ... $25.00" whole and garbled 1802's number; the model read every
field of 1802 including the MICR line. Each match is a card, and nothing
is filed until it is approved: the scans wait in storage, named by it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
import io
import re
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import OpenSession
from app.services.documents.domains.reading.changes import ReadValue
from app.services.finance.schemas import ChangeDisplayRow

CHECK = "check.attach"
PROPOSED_BY = "reading"
# A check scan is about 2.2 wide to 1 tall and well over 1000 px across;
# a logo strip is ten to one, a photo of a person is neither.
_WIDE = (1.8, 2.8)
_MIN_WIDTH_PX = 1000
# Two scans whose bottoms sit this close (PDF points) share a row.
_SAME_ROW_PT = 8
# What a lone scan must say to be read as a check: its bank's routing
# fraction ("50-17/223") or the words on a check's face. A front with
# its back beside it needs none - on #22 OCR read 1 of 19 faces whole.
_MARKERS = re.compile(r"\b\d{1,2}-\d{1,4}/\d{3,4}\b|ORDER\s+OF|DOLLARS", re.I)
# Routing, account, check number: the MICR line along the bottom.
_MICR = re.compile(r"\b(\d{9})\s+(\d{6,17})\s+(\d{3,6})\b")
_AMOUNT = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*\.\d{2})\b")
_NUMBER = re.compile(r"^\s*(\d{3,6})\b")
_PAYEE = re.compile(r"ORDER\s+OF\b[ :]*(.*)", re.I)
_DATE = re.compile(r"\bDATE\b\D{0,3}(\d{1,2}/\d{1,2}/\d{2,4})", re.I)

Read = Callable[[bytes, str], Awaitable[tuple[str, str]]]


@dataclass(frozen=True)
class CheckImage:
    """One check as the download holds it: where it sits, its scans."""

    page: int
    slot: int
    front: bytes
    back: bytes | None


@dataclass(frozen=True)
class CheckReading:
    number: str | None = None
    amount_cents: int | None = None
    payee: str | None = None
    payee_line: str | None = None
    dated: date | None = None
    account: str | None = None


def _jpeg(obj: Any) -> bytes:
    """An embedded image as JPEG, whatever the bank stored it as."""
    out = io.BytesIO()
    obj.get_bitmap(render=False).to_pil().convert("RGB").save(out, "JPEG", quality=90)
    return out.getvalue()


def split_checks(pdf: bytes) -> list[CheckImage]:
    """Every check scan in a PDF, front with its back, in page order.
    ``[]`` for a PDF with none - or bytes that are not a PDF at all."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    try:
        document = pdfium.PdfDocument(pdf)
    except Exception:  # noqa: BLE001 - not a PDF pdfium can open: no checks
        return []
    found: list[CheckImage] = []
    for index in range(len(document)):
        scans = []
        for obj in document[index].get_objects(filter=(raw.FPDF_PAGEOBJ_IMAGE,)):
            meta = obj.get_metadata()
            ratio = meta.width / max(meta.height, 1)
            if meta.width >= _MIN_WIDTH_PX and _WIDE[0] <= ratio <= _WIDE[1]:
                left, bottom, _right, _top = obj.get_bounds()
                scans.append((bottom, left, obj))
        rows: list[list[tuple[float, float, Any]]] = []
        for scan in sorted(scans, key=lambda s: (-s[0], s[1])):
            if rows and abs(rows[-1][0][0] - scan[0]) <= _SAME_ROW_PT:
                rows[-1].append(scan)
            else:
                rows.append([scan])
        for slot, row in enumerate(rows):
            sides = [obj for _b, _l, obj in sorted(row, key=lambda s: s[1])]
            found.append(
                CheckImage(
                    page=index + 1,
                    slot=slot,
                    front=_jpeg(sides[0]),
                    back=_jpeg(sides[1]) if len(sides) > 1 else None,
                )
            )
    return found


def ocr(image: bytes) -> str:
    """Tesseract's text off one scan; empty when it cannot read it."""
    from app.services.documents.domains.extraction.ocr import read_png

    read = read_png(image)
    return read[0] if read else ""


def looks_like_check(text: str) -> bool:
    return bool(_MARKERS.search(text))


def parse_check(text: str) -> CheckReading:
    """What a check's face says, from OCR or a transcription alike.
    Only what is printed AS that field: a payee is what follows "ORDER
    OF", never a guess at a name somewhere on the face."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    micr = _MICR.search(text)
    number = micr.group(3) if micr else None
    if number is None:
        number = next(
            (m.group(1) for line in lines if (m := _NUMBER.match(line))), None
        )
    amount = _AMOUNT.search(text)
    payee = payee_line = None
    for n, line in enumerate(lines):
        if found := _PAYEE.search(line):
            said = found.group(1).strip() or (
                lines[n + 1] if n + 1 < len(lines) else ""
            )
            said = _AMOUNT.split(said)[0].strip(" $:")
            if said:
                payee, payee_line = said, f"ORDER OF {said}"
            break
    dated = None
    if when := _DATE.search(text):
        for shape in ("%m/%d/%y", "%m/%d/%Y"):
            try:
                dated = datetime.strptime(when.group(1), shape).date()
                break
            except ValueError:
                continue
    return CheckReading(
        number=number,
        amount_cents=round(float(amount.group(1).replace(",", "")) * 100)
        if amount
        else None,
        payee=payee,
        payee_line=payee_line,
        dated=dated,
        account=micr.group(2) if micr else None,
    )


def match(reading: CheckReading, rows: list[Any]) -> Any | None:
    """The one ledger row this check is: its number, and its amount when
    the face gave one. Two candidates is no answer."""
    if reading.number is None:
        return None
    same = [r for r in rows if r.number == reading.number]
    if reading.amount_cents is not None:
        same = [r for r in same if abs(r.amount) == reading.amount_cents]
    return same[0] if len(same) == 1 else None


async def propose_checks(
    open_session: OpenSession,
    document_id: int,
    *,
    owner_user_id: int | None = None,
    read: Read | None = None,
) -> int:
    """A card per check in this document that matches a ledger row and
    is not filed or asked about already. Returns how many were made.
    Reads the scans outside any session: the model is slow, and an open
    session is the whole app's write lock."""
    from app.core.storage import get_storage
    from app.services.documents.service import DocumentService
    from app.services.finance.domains.writes.queue import propose

    async with open_session() as db:
        document = await DocumentService(db).get(document_id)
        if document is None or "pdf" not in (document.media_type or ""):
            return 0
        pdf = await get_storage().get(document.storage_key)
    # The ledger only for a PDF that holds check scans: most do not.
    found = split_checks(pdf) if pdf else []
    if not found:
        return 0
    async with open_session() as db:
        rows, done = await _ledger(db, document_id, owner_user_id)
    store = get_storage()
    cards: list[dict[str, Any]] = []
    for check in found:
        if (check.page, check.slot) in done.slots:
            continue
        text = ocr(check.front)
        # A front with its back beside it is a check by its shape; a lone
        # scan must say so. Either way no card without a ledger match.
        if check.back is None and not looks_like_check(text):
            continue
        reading = parse_check(text)
        row = match(reading, rows)
        # The model only where it earns its call: OCR could not pin the
        # row, or the row has no payee and only handwriting names one.
        if (row is None or row.merchant_id is None) and read is not None:
            seen = parse_check((await read(check.front, "image/jpeg"))[0])
            if found := match(seen, rows):
                row, reading = found, seen
        if row is None or row.id in done.transactions:
            continue
        cards.append(
            {
                "document_id": document_id,
                "page": check.page,
                "slot": check.slot,
                "transaction_id": row.id,
                "number": reading.number,
                "front_key": await store.put(check.front, content_type="image/jpeg"),
                "back_key": await store.put(check.back, content_type="image/jpeg")
                if check.back
                else None,
                "payee": {
                    "value": reading.payee,
                    "page": check.page,
                    "because": reading.payee_line,
                }
                if reading.payee and row.merchant_id is None
                else None,
            }
        )
    async with open_session() as db:
        for payload in cards:
            await propose(
                db,
                CHECK,
                payload,
                owner_user_id=owner_user_id,
                proposed_by_agent=PROPOSED_BY,
            )
        await db.commit()
    return len(cards)


@dataclass(frozen=True)
class _CheckRow:
    id: int
    number: str
    amount: int
    merchant_id: int | None


@dataclass(frozen=True)
class _Done:
    slots: set[tuple[int, int]]
    transactions: set[int]


async def _ledger(
    db: AsyncSession, document_id: int, owner_user_id: int | None
) -> tuple[list[_CheckRow], _Done]:
    """The check rows a scan can be, and what is already asked or filed:
    a card for this document's slot, or a row wearing a receipt."""
    from sqlmodel import col, or_, select

    from app.services.documents.queries import tagged_with
    from app.services.finance.constants import transaction_tag
    from app.services.finance.domains.ledger.queries.filters import not_duplicate
    from app.services.finance.domains.writes.queue import list_changes
    from app.services.finance.models import FinanceTransaction as T

    query = select(T).where(
        col(T.deleted_at).is_(None),
        not_duplicate(),
        or_(col(T.check_number).is_not(None), col(T.name).ilike("check%")),
    )
    if owner_user_id is not None:
        query = query.where(T.owner_user_id == owner_user_id)
    rows = [
        _CheckRow(int(t.id or 0), number, t.amount, t.merchant_id)
        for t in (await db.exec(query)).all()
        if (number := t.check_number or _row_number(t.name))
    ]
    asked = {
        (int(c.payload["page"]), int(c.payload["slot"]))
        for status in ("pending", "approved")
        for c in await list_changes(db, status=status)
        if c.change_type == CHECK and c.payload.get("document_id") == document_id
    }
    filed = await tagged_with(db, [transaction_tag(r.id) for r in rows])
    receipted = {r.id for r in rows if filed.get(transaction_tag(r.id))}
    return rows, _Done(asked, receipted)


def _row_number(name: str | None) -> str | None:
    found = re.search(r"\bCHECK\s*#?\s*(\d{3,6})\b", name or "", re.I)
    return found.group(1) if found else None


# --- the card ----------------------------------------------------------------


class CheckPayload(BaseModel):
    """One check's scans for its ledger row, and the payee read off its
    face where the row has none. Keys name the scans in storage."""

    model_config = ConfigDict(extra="forbid")

    document_id: int
    page: int
    slot: int
    transaction_id: int
    number: str
    front_key: str
    back_key: str | None = None
    payee: ReadValue | None = None


async def check_execute(
    db: AsyncSession, payload: CheckPayload, owner_user_id: int | None
) -> dict[str, Any]:
    """File the scans on the row as its receipt; name its payee."""
    from app.core.storage import get_storage
    from app.services.documents.service import DocumentService
    from app.services.finance.constants import transaction_tag
    from app.services.finance.domains.writes.curation import (
        AssignPayeePayload,
        assign_payee_execute,
    )

    store = get_storage()
    documents = DocumentService(db)
    filed: list[int] = []
    for key, title in (
        (payload.front_key, f"Check {payload.number}"),
        (payload.back_key, f"Check {payload.number} (back)"),
    ):
        if key is None:
            continue
        data = await store.get(key)
        if data is None:
            raise ValueError(f"The scan of check {payload.number} is gone; read again.")
        document = await documents.ingest(
            data,
            title=title,
            kind="receipt",
            media_type="image/jpeg",
            owner_user_id=owner_user_id,
            source="reading",
        )
        await documents.tag(
            int(document.id or 0), transaction_tag(payload.transaction_id)
        )
        filed.append(int(document.id or 0))
    if payload.payee is not None:
        await assign_payee_execute(
            db,
            AssignPayeePayload(
                transaction_id=payload.transaction_id, payee=payload.payee.value
            ),
            owner_user_id,
        )
    await db.flush()
    return {"transaction_id": payload.transaction_id, "documents": filed}


async def check_describe(
    db: AsyncSession, payload: CheckPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    from app.services.finance.domains.writes.display import txn_row

    _txn, subject = await txn_row(db, payload.transaction_id, owner_user_id)
    rows = [
        subject,
        ChangeDisplayRow(
            label="Check",
            value=f"#{payload.number}, front and back",
            note=f"page {payload.page}",
            document_id=payload.document_id,
            page=payload.page,
        ),
    ]
    if payload.payee is not None:
        rows.append(
            ChangeDisplayRow(
                label="Payee",
                value=f"- → {payload.payee.value}",
                note=payload.payee.cited(),
                document_id=payload.document_id,
                page=payload.page,
            )
        )
    return rows
