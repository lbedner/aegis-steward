"""Web frontend component - Jinja2 + htmx + Alpine.js.

Pages are served at ``/`` by the same webserver that hosts the API and the
Flet dashboard at ``/dashboard``. The pieces live beside this module:
``assets.py`` (fingerprinted URLs, cache policy), ``filters.py`` (Jinja
filters), ``rendering.py`` (the environment, ``render``, ``with_toast``),
``nav.py`` (the section list). Routes live under ``routes/``.
"""

from fastapi import APIRouter


def create_web_frontend_app() -> APIRouter:
    """Create the web frontend router: the root redirect plus every section."""
    router = APIRouter()

    from app.components.web_frontend.routes.finance import router as finance_router
    from app.components.web_frontend.routes.jobs import router as jobs_router
    from app.components.web_frontend.routes.pages import router as pages_router

    router.include_router(pages_router)
    router.include_router(finance_router)
    router.include_router(jobs_router)

    return router
