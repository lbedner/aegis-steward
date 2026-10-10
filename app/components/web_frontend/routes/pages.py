"""Routes that are not a section: the root redirect, and Overseer's pages
behind its one gate.

Section pages live under ``routes/finance/``, one module per sidebar entry.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

from app.components.web_frontend.overseer_access import overseer_gate
from app.components.web_frontend.routes import overseer as overseer_routes
from app.components.web_frontend.routes.partials import (
    overseer_ai as overseer_ai_partials,
)
from app.components.web_frontend.routes.partials import (
    overseer_code as overseer_code_partials,
)
from app.components.web_frontend.routes.partials import (
    overseer_comms as overseer_comms_partials,
)
from app.components.web_frontend.routes.partials import (
    overseer_database as overseer_database_partials,
)
from app.components.web_frontend.routes.partials import (
    overseer_documents as overseer_documents_partials,
)
from app.components.web_frontend.routes.partials import (
    overseer_errors as overseer_errors_partials,
)
from app.components.web_frontend.routes.partials import (
    overseer_inference as overseer_inference_partials,
)
from app.components.web_frontend.routes.partials import (
    overseer_redis as overseer_redis_partials,
)
from app.components.web_frontend.routes.partials import (
    overseer_runtime as overseer_runtime_partials,
)
from app.components.web_frontend.routes.partials import (
    overseer_scheduler as overseer_scheduler_partials,
)
from app.components.web_frontend.routes.partials import (
    overseer_secrets as overseer_secrets_partials,
)
from app.components.web_frontend.routes.partials import (
    overseer_server as overseer_server_partials,
)
from app.components.web_frontend.routes.partials import (
    overseer_storage as overseer_storage_partials,
)
from app.components.web_frontend.routes.partials import (
    overseer_worker as overseer_worker_partials,
)

# Every Overseer page, stream and action passes one gate (overseer_access).
router = APIRouter(dependencies=[Depends(overseer_gate)])


@router.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """There is no landing page; the app opens on Overview."""
    return RedirectResponse(url="/overview", status_code=303)


# Overseer pages' dialogs and forms (htmx fragments), then its pages: a
# partial's path wins over the pages' catch-all routes.
router.include_router(overseer_scheduler_partials.router)
router.include_router(overseer_secrets_partials.router)
router.include_router(overseer_redis_partials.router)
router.include_router(overseer_storage_partials.router)
router.include_router(overseer_server_partials.router)
router.include_router(overseer_server_partials.load_tests_router)
router.include_router(overseer_server_partials.requests_router)
router.include_router(overseer_runtime_partials.router)
router.include_router(overseer_database_partials.router)
router.include_router(overseer_documents_partials.router)
router.include_router(overseer_comms_partials.router)
router.include_router(overseer_ai_partials.router)
router.include_router(overseer_inference_partials.router)
router.include_router(overseer_worker_partials.router)
router.include_router(overseer_errors_partials.router)
router.include_router(overseer_code_partials.router)
router.include_router(overseer_routes.router)
