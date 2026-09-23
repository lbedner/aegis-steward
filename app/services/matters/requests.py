"""Obligations: what was asked, by when, and what still stands.

The status of a request is DERIVED from its items, and overdue is
derived from the due date and the clock. Neither is stored: a request
that says "satisfied" while an item says "needed" is a record arguing
with itself, and a stored overdue flag is wrong from the first midnight
after somebody writes it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.clock import utcnow
from app.core.schema import require_one_of
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
                    # The kind rides with the ask. ``add_item`` had always
                    # honoured it and this dropped it, so every ask a
                    # letter filed came out a "document".
                    kind=str(item.get("kind") or "document"),
                    ask=(item.get("ask") or "").strip() or None,
                    subject_party_id=item.get("subject_party_id"),
                    as_of=item.get("as_of"),
                    status="needed",
                )
            )
        await self.db.flush()
        await self._expected_letter_arrived(matter_id, received_on)
        return request

    async def _expected_letter_arrived(
        self, matter_id: int, received_on: date | None
    ) -> None:
        """A letter inside the window before the matter's next expected
        date IS that letter: move the expectation on by the cadence, or
        clear a one-off (ST-11). A follow-up in March is not next August's
        renewal, so anything earlier than the window leaves it alone."""
        from datetime import timedelta

        from app.services.finance.utils import current_date
        from app.services.matters.deadlines import EXPECTED_WITHIN_DAYS
        from app.services.matters.matters import MatterService, next_after

        matters = MatterService(self.db)
        matter = await matters.get(matter_id)
        if matter is None or matter.next_expected_on is None:
            return
        arrived = received_on or current_date()
        if arrived < matter.next_expected_on - timedelta(days=EXPECTED_WITHIN_DAYS):
            return
        upcoming: date | None = None
        if matter.cadence:
            upcoming = next_after(matter.next_expected_on, matter.cadence)
            while upcoming <= arrived:
                upcoming = next_after(upcoming, matter.cadence)
        await matters.set_cadence(
            matter_id, cadence=matter.cadence, next_expected_on=upcoming
        )

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

    async def items_of(self, request_ids: list[int]) -> dict[int, list[RequestItem]]:
        """The items of several requests, in one query.

        ``items`` asked per request, which is a query per letter for
        anything drawing a whole matter.
        """
        if not request_ids:
            return {}
        found: dict[int, list[RequestItem]] = {one: [] for one in request_ids}
        rows = (
            await self.db.exec(
                select(RequestItem)
                .where(col(RequestItem.request_id).in_(request_ids))
                .order_by(col(RequestItem.ordinal))
            )
        ).all()
        for row in rows:
            found.setdefault(row.request_id, []).append(row)
        return found

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

    async def citing(self, document_id: int) -> list[Request]:
        """The requests already read off this letter. A second reading
        that files them again is a duplicate matter, not a correction."""
        query = select(Request).where(
            col(Request.document_id) == document_id,
            col(Request.deleted_at).is_(None),
        )
        return list((await self.db.exec(query)).all())

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
        require_one_of(status, ITEM_STATUSES)
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
        from app.services.matters.evidence import link

        item = await self.db.get(RequestItem, item_id)
        if item is None:
            return None
        # Through the LINK, the one home for "what answers this". The
        # old column is still written until nothing reads it.
        await link(self.db, item_id, document_id=document_id, note=title)
        item.document_id = document_id
        self.db.add(item)
        await self.db.flush()
        return await self.mark(item_id, "satisfied", title)

    async def detach(self, item_id: int) -> RequestItem | None:
        """Wrong paper. Takes EVERY piece of evidence off this item.

        The item stands again only if nothing is left, which the link
        layer decides: one of three removed leaves two, and two is still
        an answer. This is the blunt "none of it was right" door; the
        precise one is ``evidence.unlink``.
        """
        from app.services.matters.evidence import unlink

        item = await self.db.get(RequestItem, item_id)
        if item is None:
            return None
        await unlink(self.db, item_id)
        item.document_id = None
        self.db.add(item)
        await self.db.flush()
        return await self.db.get(RequestItem, item_id)

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
