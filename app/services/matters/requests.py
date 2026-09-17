"""Obligations: what was asked, by when, and what still stands.

The status of a request is DERIVED from its items, and overdue is
derived from the due date and the clock. Neither is stored: a request
that says "satisfied" while an item says "needed" is a record arguing
with itself, and a stored overdue flag is wrong from the first midnight
after somebody writes it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
import re
from typing import Any

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.clock import utcnow
from app.services.matters.models import (
    ITEM_KINDS,
    ITEM_STATUSES,
    REQUEST_STATUSES,
    Request,
    RequestItem,
)

# An item that no longer needs anything: satisfied, or agreed not to
# apply. "Waived" is the agency's word and "not applicable" is yours,
# and both close an item without it ever being answered - which is why
# neither can be inferred and both have to be recorded.
SETTLED = ("satisfied", "not_applicable", "waived")


class RequestService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def record(
        self,
        *,
        matter_id: int,
        items: list[dict[str, Any]] | None = None,
        document_id: int | None = None,
        requester_party_id: int | None = None,
        received_on: date | None = None,
        due_on: date | None = None,
        owner_user_id: int | None = None,
        note: str | None = None,
    ) -> Request:
        """A letter's demands, in the order the letter made them."""
        request = Request(
            owner_user_id=owner_user_id,
            matter_id=matter_id,
            document_id=document_id,
            requester_party_id=requester_party_id,
            received_on=received_on,
            due_on=due_on,
            status="open",
            note=(note or "").strip() or None,
        )
        self.db.add(request)
        await self.db.flush()
        for ordinal, item in enumerate(items or [], start=1):
            asked = " ".join(str(item.get("asked") or "").split())
            if not asked:
                raise ValueError("An item needs the sentence that was asked.")
            self.db.add(
                RequestItem(
                    request_id=request.id,
                    ordinal=item.get("ordinal") or ordinal,
                    asked=asked,
                    ask=(item.get("ask") or "").strip() or None,
                    subject_party_id=item.get("subject_party_id"),
                    as_of=item.get("as_of"),
                    status="needed",
                )
            )
        await self.db.flush()
        return request

    async def add_item(
        self,
        request_id: int,
        *,
        asked: str,
        kind: str = "document",
        ask: str | None = None,
        as_of: date | None = None,
        alternative_to: int | None = None,
    ) -> RequestItem:
        """One more ask on a request that already exists.

        Letters are read twice. The second reading splits an item in two
        ("proof of income" was two pensions) or finds the sentence that
        was skipped, and a request that can only be written at intake is
        one people keep a paper list beside.

        ``alternative_to`` puts this ask in the same group as another:
        the county will take any ONE of them.
        """
        written = " ".join((asked or "").split())
        if not written:
            raise ValueError("An item needs the sentence that was asked.")
        if kind not in dict(ITEM_KINDS):
            raise ValueError(f"One of: {', '.join(k for k, _ in ITEM_KINDS)}.")
        group: str | None = None
        if alternative_to:
            sibling = await self.db.get(RequestItem, alternative_to)
            if sibling is not None:
                group = sibling.option_group or f"g{sibling.id}"
                if sibling.option_group != group:
                    sibling.option_group = group
                    self.db.add(sibling)
        existing = await self.items(request_id)
        item = RequestItem(
            request_id=request_id,
            ordinal=max((one.ordinal for one in existing), default=0) + 1,
            asked=written,
            kind=kind,
            ask=(ask or "").strip() or None,
            as_of=as_of,
            option_group=group,
            status="needed",
        )
        self.db.add(item)
        await self.db.flush()
        await self._settle(request_id)
        return item

    async def get(self, request_id: int) -> Request | None:
        request = await self.db.get(Request, request_id)
        return request if request and request.deleted_at is None else None

    async def item(self, item_id: int) -> RequestItem | None:
        return await self.db.get(RequestItem, item_id)

    async def items(self, request_id: int) -> list[RequestItem]:
        return list(
            (
                await self.db.exec(
                    select(RequestItem)
                    .where(RequestItem.request_id == request_id)
                    .order_by(col(RequestItem.ordinal))
                )
            ).all()
        )

    async def overdue(self, today: date | None = None) -> list[Request]:
        """Every open request whose deadline has passed - the sidebar's
        mark and the list's red rows read from this one query, so the
        two cannot disagree about what is late."""
        return list(
            (
                await self.db.exec(
                    select(Request)
                    .where(Request.status == "open")
                    .where(col(Request.due_on) < (today or datetime.now(UTC).date()))
                    .where(col(Request.deleted_at).is_(None))
                    .order_by(col(Request.due_on))
                )
            ).all()
        )

    async def from_party(self, party_id: int) -> list[Request]:
        """The letters this party sent: every request they are the
        requester of, newest deadline first."""
        return list(
            (
                await self.db.exec(
                    select(Request)
                    .where(Request.requester_party_id == party_id)
                    .where(col(Request.deleted_at).is_(None))
                    .order_by(col(Request.due_on).desc())
                )
            ).all()
        )

    async def for_matter(self, matter_id: int) -> list[Request]:
        return list(
            (
                await self.db.exec(
                    select(Request)
                    .where(Request.matter_id == matter_id)
                    .where(col(Request.deleted_at).is_(None))
                    .order_by(col(Request.due_on))
                )
            ).all()
        )

    async def amend(
        self,
        item_id: int,
        *,
        asked: str | None = None,
        kind: str | None = None,
        ask: str | None = None,
        as_of: date | None = None,
    ) -> RequestItem | None:
        """Correct what an item says.

        The sentence is the county's, not ours - which is exactly why a
        mistyped one has to be fixable. Recording it wrong and then
        answering the wrong question is worse than the typo, and a
        record nobody can correct is one people keep outside the app.
        """
        item = await self.db.get(RequestItem, item_id)
        if item is None:
            return None
        if asked is not None:
            written = " ".join(asked.split())
            if not written:
                raise ValueError("An item needs the sentence that was asked.")
            item.asked = written
        if kind:
            if kind not in dict(ITEM_KINDS):
                raise ValueError(f"One of: {', '.join(k for k, _ in ITEM_KINDS)}.")
            item.kind = kind
        if ask is not None:
            item.ask = ask.strip() or None
        if as_of is not None:
            item.as_of = as_of
        item.updated_at = utcnow()
        self.db.add(item)
        await self.db.flush()
        return item

    async def mark(
        self, item_id: int, status: str, resolution: str | None = None
    ) -> RequestItem | None:
        """Settle one item, and let the request follow.

        The request's own status is recomputed here rather than set: a
        request reads satisfied when every item does, and never because
        somebody said so while an item still stands.
        """
        if status not in ITEM_STATUSES:
            raise ValueError(f"One of: {', '.join(ITEM_STATUSES)}.")
        item = await self.db.get(RequestItem, item_id)
        if item is None:
            return None
        item.status = status
        item.resolution = (resolution or "").strip() or None
        item.updated_at = utcnow()
        self.db.add(item)
        await self.db.flush()
        await self._settle(item.request_id)
        return item

    async def attach(
        self, item_id: int, document_id: int, title: str | None = None
    ) -> RequestItem | None:
        """The paper that answers this item.

        Attaching IS the answer: somebody who has found the power of
        attorney and put it here is not then asked to say separately
        that the item is satisfied. It can be put back like any other
        mark if the paper turns out to be the wrong one.
        """
        item = await self.db.get(RequestItem, item_id)
        if item is None:
            return None
        item.document_id = document_id
        self.db.add(item)
        await self.db.flush()
        return await self.mark(item_id, "satisfied", title)

    async def detach(self, item_id: int) -> RequestItem | None:
        """Wrong paper. The item stands again, because an item whose
        only evidence has been taken away is not answered."""
        item = await self.db.get(RequestItem, item_id)
        if item is None:
            return None
        item.document_id = None
        self.db.add(item)
        await self.db.flush()
        return await self.mark(item_id, "needed")

    async def cite(self, request_id: int, document_id: int | None) -> Request | None:
        """The letter this request came from.

        It should have been the first thing filed and usually is not:
        somebody types the asks while reading the page, and the scan
        lands afterwards. Setting it later is the normal case, not the
        exception.
        """
        request = await self.get(request_id)
        if request is None:
            return None
        request.document_id = document_id
        request.updated_at = utcnow()
        self.db.add(request)
        await self.db.flush()
        return request

    async def waive(self, request_id: int) -> Request | None:
        """The agency stopped asking. Whole-request only: an item nobody
        has answered is not the same as one nobody needs any more."""
        request = await self.get(request_id)
        if request is None:
            return None
        request.status = "waived"
        request.updated_at = utcnow()
        self.db.add(request)
        await self.db.flush()
        return request

    async def _settle(self, request_id: int) -> None:
        request = await self.get(request_id)
        if request is None or request.status == "waived":
            return
        items = await self.items(request_id)
        settled, total = standing(items)
        done = bool(items) and settled == total
        request.status = "satisfied" if done else "open"
        request.updated_at = utcnow()
        self.db.add(request)
        await self.db.flush()


def overdue(request: Request, today: date | None = None) -> bool:
    """Past its due date and still wanting something.

    Derived, never stored. A flag written last night is a lie this
    morning, and the one thing a deadline must not do is go quiet.
    """
    if request.status in ("satisfied", "waived") or request.due_on is None:
        return False
    return request.due_on < (today or datetime.now(UTC).date())


def standing(items: list[RequestItem]) -> tuple[int, int]:
    """(settled, total) - what a page says without counting twice.

    A group of alternatives counts ONCE and is settled by any member:
    the POA, the designation and the attestation are three ways to
    answer one demand, and "1 of 5" on a letter that asked for three
    things is a page arguing with the letter.
    """
    units: dict[str, bool] = {}
    for item in items:
        key = item.option_group or f"item:{item.id}"
        units[key] = units.get(key, False) or item.status in SETTLED
    return sum(1 for done in units.values() if done), len(units)


assert set(SETTLED) <= set(ITEM_STATUSES)
assert "overdue" not in REQUEST_STATUSES, "overdue is derived, never stored"


async def drawn(
    db: AsyncSession, request: Request, today: date | None = None
) -> dict[str, Any]:
    """One request as a page draws it.

    The status and the lateness are computed HERE, once, so the template
    never has to decide what "satisfied" means and no second copy of the
    rule can drift from this one.
    """
    items = await RequestService(db).items(request.id)
    settled, total = standing(items)
    papers = await titles(db, [item.document_id for item in items])
    late = overdue(request, today)
    letter = (await titles(db, [request.document_id])).get(request.document_id or 0)
    return {
        "id": request.id,
        "matter_id": request.matter_id,
        # The page it all came from, read beside the asks it produced.
        "document_id": request.document_id,
        "letter": letter,
        "received_on": request.received_on,
        "due_on": request.due_on,
        "status": request.status,
        "overdue": late,
        "tone": "error" if late else ("ok" if request.status != "open" else "muted"),
        "settled": settled,
        "total": total,
        "note": request.note,
        # Grouped for the page: alternatives are ONE step carrying its
        # options, because the county will take any one of them. Not
        # "items" as a key either way - Jinja resolves ``thing.items``
        # to the dict method before the key, and the loop walks a
        # builtin instead.
        "steps": steps(items, papers),
    }


_BULLET = re.compile(r"^(?:[-*\u2022]|\d{1,2}[.)])\s*")


async def titles(
    db: AsyncSession, document_ids: list[int | None]
) -> dict[int, dict[str, Any]]:
    """The attached documents as a page names them, in ONE query.

    A page draws a dozen items and a title per item is a dozen round
    trips, which is the N+1 the house rules name outright.
    """
    from app.services.documents.models import Document

    wanted = [one for one in document_ids if one]
    if not wanted:
        return {}
    rows = (await db.exec(select(Document).where(col(Document.id).in_(wanted)))).all()
    return {
        row.id: {"id": row.id, "title": row.title, "media_type": row.media_type}
        for row in rows
        if row.id is not None
    }


def steps(
    items: list[RequestItem], papers: dict[int, dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """The asks as work: one step per demand, alternatives inside it.

    The step takes its wording from the first option and its state from
    the group - settled when ANY option is, because that is what "we
    will take any one of these" means.
    """
    order: list[str] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        key = item.option_group or f"item:{item.id}"
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(_option(item, papers or {}))
    out = []
    for key in order:
        options = grouped[key]
        lead = options[0]
        out.append(
            {
                "id": key,
                "asked": lead["asked"],
                "kind": lead["kind"],
                "kind_label": lead["kind_label"],
                "as_of": lead["as_of"],
                "settled": any(option["settled"] for option in options),
                "options": options,
            }
        )
    return out


def _option(item: RequestItem, papers: dict[int, dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": item.id,
        "ordinal": item.ordinal,
        "asked": item.asked,
        "kind": item.kind,
        "kind_label": dict(ITEM_KINDS).get(item.kind, item.kind),
        "ask": item.ask,
        "as_of": item.as_of,
        "status": item.status,
        "settled": item.status in SETTLED,
        "resolution": item.resolution,
        "document": papers.get(item.document_id or 0),
    }


def asked_lines(text: str) -> list[dict[str, Any]]:
    """A letter's demands as somebody types them: one ask per line.

    Blank lines and a leading bullet or number are dropped - people
    paste from the letter, and the letter numbers its own list.
    """
    items: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        cleaned = _BULLET.sub("", line.strip()).strip()
        if cleaned:
            items.append({"asked": cleaned})
    return items
