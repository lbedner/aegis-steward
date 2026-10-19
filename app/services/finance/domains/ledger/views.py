"""Saved register views (#326): a filter set kept under a name.

The register's filters already live in its URL, so a view is that URL,
named - opening one is following a link, and nothing about a filter has
to be stored twice.
"""

from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger import queries
from app.services.finance.models import FinanceRegisterView


async def list_views(
    db: AsyncSession, owner_user_id: int | None
) -> list[FinanceRegisterView]:
    """The owner's views, by name."""
    return await queries.views_for(db, owner_user_id)


async def save_view(
    db: AsyncSession, owner_user_id: int | None, name: str, url: str
) -> FinanceRegisterView:
    view = FinanceRegisterView(owner_user_id=owner_user_id, name=name.strip(), url=url)
    db.add(view)
    await db.flush()
    return view


async def delete_view(
    db: AsyncSession, owner_user_id: int | None, view_id: int
) -> None:
    view = await db.get(FinanceRegisterView, view_id)
    if view is not None and view.owner_user_id == owner_user_id:
        await db.delete(view)
        await db.flush()
