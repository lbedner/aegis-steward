"""The near misses on offer (#317), and when an offer goes.

An offer holds both its legs until someone answers it. A sure pair -
scored, or a payment the loan's own history confirms - takes a leg
from it, and an offer whose leg was deleted can never be answered:
either way the offer goes, and its Attention item with it. A pre-auth
that posted takes its confirmed pair too, so the other leg pairs again.
"""

from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.detection import queries
from app.services.finance.domains.ledger.queries import live_transactions_by_ids
from app.services.finance.models import FinanceInsight, FinanceTransfer

from .shared import SUGGESTED


class Offers:
    """The owner's offers by leg, read once a pass."""

    def __init__(self, transfers: list[FinanceTransfer]) -> None:
        self.by_leg = {
            leg: offer
            for offer in transfers
            if offer.status == "suggested"
            for leg in (offer.from_transaction_id, offer.to_transaction_id)
            if leg is not None
        }
        self.taken: list[int] = []

    def holding(self, *legs: int | None) -> dict[int, FinanceTransfer]:
        """The offers holding any of ``legs``, by id."""
        return {
            offer.id: offer
            for leg in legs
            if leg is not None
            and (offer := self.by_leg.get(leg)) is not None
            and offer.id is not None
        }

    async def take(self, db: AsyncSession, held: dict[int, FinanceTransfer]) -> None:
        """Inside the sure pair's savepoint: the offers it takes legs
        from, deleted, so its own insert holds the legs."""
        for offer in held.values():
            await db.delete(offer)
        await db.flush()

    def taken_by(self, held: dict[int, FinanceTransfer]) -> None:
        """The pair was made: its offers are gone for this pass too."""
        self.taken.extend(held)
        for offer in held.values():
            for leg in (offer.from_transaction_id, offer.to_transaction_id):
                self.by_leg.pop(leg or 0, None)

    async def withdraw(self, db: AsyncSession) -> None:
        """The Attention items of every offer taken: answered."""
        await _dismiss(db, self.taken)
        self.taken = []

    async def drop_dead(self, db: AsyncSession) -> None:
        """Offers whose leg is no longer a live row (deleted, or a
        pending charge that posted as another), gone."""
        legs = list(self.by_leg)
        if not legs:
            return
        live = {row.id for row in await live_transactions_by_ids(db, legs)}
        dead = {
            self.by_leg[leg].id: self.by_leg[leg] for leg in legs if leg not in live
        }
        await _drop(db, list(dead.values()))
        self.by_leg = {
            leg: offer for leg, offer in self.by_leg.items() if offer.id not in dead
        }


async def drop_offers_on(db: AsyncSession, leg_ids: list[int]) -> None:
    """Every offer holding one of ``leg_ids``, gone with its Attention
    item: an offer whose half is deleted can never be answered. A
    confirmed pair stays, so an Undo of the delete has it back."""
    if not leg_ids:
        return
    await _drop(db, await queries.offers_holding(db, leg_ids))


async def drop_pairs_on(db: AsyncSession, leg_ids: list[int]) -> None:
    """Every live pair holding one of ``leg_ids`` - confirmed or offered -
    gone: a pre-auth that posted or dropped (#540), whose pair would claim
    its other leg for good."""
    if not leg_ids:
        return
    await _drop(db, await queries.pairs_holding(db, leg_ids))


async def _drop(db: AsyncSession, offers: list[FinanceTransfer]) -> None:
    if not offers:
        return
    for offer in offers:
        await db.delete(offer)
    await db.flush()
    await _dismiss(db, [offer.id for offer in offers if offer.id is not None])


async def _dismiss(db: AsyncSession, offer_ids: list[int]) -> None:
    if not offer_ids:
        return
    for item in await queries.insight_rows_where(
        db,
        [
            FinanceInsight.dedup_key.in_([f"{SUGGESTED}:{i}" for i in offer_ids]),
            FinanceInsight.status == "new",
        ],
    ):
        item.status = "dismissed"
        db.add(item)
