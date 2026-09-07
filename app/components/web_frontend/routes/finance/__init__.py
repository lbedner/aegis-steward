"""Section routes, one module per ``NAV`` entry.

Each module owns one sidebar section: its page route now, its fragment
and action routes as the phases land. Mounted at ``/`` by
``create_web_frontend_app()``.
"""

from fastapi import APIRouter

from app.components.web_frontend.routes.finance import (
    accounts,
    bills,
    budget,
    overview,
    projected,
    review,
    settings,
)

router = APIRouter()
router.include_router(overview.router)
router.include_router(accounts.router)
router.include_router(bills.router)
router.include_router(projected.router)
router.include_router(budget.router)
router.include_router(review.router)
router.include_router(settings.router)
