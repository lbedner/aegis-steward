"""FastAPI dependency providers for the finance service.

Mirrors ``payment/deps.py``. ``get_owner_user_id`` centralizes owner scoping in
one place: it resolves to the authenticated user's id when the auth service is
present, else ``None`` (single-user / standalone finance) — so route handlers
stay auth-agnostic and never repeat the auth guard per endpoint.
"""

from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import get_async_db
from app.services.finance.service import FinanceService


async def get_finance_service(
    db: AsyncSession = Depends(get_async_db),
) -> FinanceService:
    return FinanceService(db)


async def get_owner_user_id() -> int | None:
    """No auth service — finance is single-user, so rows are unscoped."""
    return None
