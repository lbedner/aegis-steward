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

    from app.components.web_frontend.routes.chat import router as chat_router
    from app.components.web_frontend.routes.chat_cards import (
        router as chat_cards_router,
    )
    from app.components.web_frontend.routes.chat_live import router as chat_live_router
    from app.components.web_frontend.routes.chat_speech import (
        router as chat_speech_router,
    )
    from app.components.web_frontend.routes.chat_voices import (
        router as chat_voices_router,
    )
    from app.components.web_frontend.routes.contacts import router as contacts_router
    from app.components.web_frontend.routes.documents import router as documents_router
    from app.components.web_frontend.routes.facts import (
        accounts as account_facts_router,
    )
    from app.components.web_frontend.routes.facts import router as facts_router
    from app.components.web_frontend.routes.finance import router as finance_router
    from app.components.web_frontend.routes.finance.positions import (
        router as positions_router,
    )
    from app.components.web_frontend.routes.icons import router as icons_router
    from app.components.web_frontend.routes.jobs import router as jobs_router
    from app.components.web_frontend.routes.mail import router as mail_router
    from app.components.web_frontend.routes.matter_papers import (
        router as papers_router,
    )
    from app.components.web_frontend.routes.matter_timeline import (
        router as timeline_router,
    )
    from app.components.web_frontend.routes.matters import router as matters_router
    from app.components.web_frontend.routes.pages import router as pages_router
    from app.components.web_frontend.routes.policies import router as policies_router
    from app.components.web_frontend.routes.request_items import (
        router as items_router,
    )
    from app.components.web_frontend.routes.requests import router as requests_router
    from app.components.web_frontend.routes.signins import router as signins_router

    router.include_router(pages_router)
    router.include_router(finance_router)
    router.include_router(jobs_router)
    router.include_router(matters_router)
    router.include_router(papers_router)
    router.include_router(timeline_router)
    router.include_router(requests_router)
    router.include_router(items_router)
    router.include_router(facts_router)
    router.include_router(account_facts_router)
    router.include_router(signins_router)
    router.include_router(policies_router)
    router.include_router(positions_router)
    router.include_router(contacts_router)
    router.include_router(documents_router)
    router.include_router(mail_router)
    router.include_router(icons_router)
    router.include_router(chat_router)
    router.include_router(chat_speech_router)
    router.include_router(chat_live_router)
    router.include_router(chat_cards_router)
    router.include_router(chat_voices_router)

    return router
