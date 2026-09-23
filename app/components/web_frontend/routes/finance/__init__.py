"""Section routes, one module per ``NAV`` entry.

Each module owns one sidebar section: its page route now, its fragment
and action routes as the phases land. Mounted at ``/`` by
``create_web_frontend_app()``.
"""

from fastapi import APIRouter

from app.components.web_frontend.routes.finance import (
    account_manage,
    account_naming,
    accounts,
    bills,
    bills_match,
    budget,
    budget_envelopes,
    budget_goals,
    cover,
    documents,
    imports,
    institutions,
    overview,
    projected,
    review,
    review_edit,
    review_resolve,
    settings,
    transactions,
)

router = APIRouter()
router.include_router(overview.router)
router.include_router(accounts.router)
router.include_router(account_manage.router)
router.include_router(account_naming.router)
router.include_router(cover.router)
router.include_router(documents.router)
router.include_router(imports.router)
router.include_router(institutions.router)
router.include_router(bills.router)
router.include_router(bills_match.router)
router.include_router(projected.router)
router.include_router(budget.router)
router.include_router(budget_goals.router)
router.include_router(budget_envelopes.router)
# Before review: its edit routes must beat review's verb wildcard.
router.include_router(review_edit.router)
router.include_router(review_resolve.router)
router.include_router(review.router)
router.include_router(settings.router)
router.include_router(transactions.router)
