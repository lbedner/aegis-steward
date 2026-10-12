"""Routes that are not a section: the root redirect, and Overseer's pages
behind its one gate.

Section pages live under ``routes/finance/``, one module per sidebar entry.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm

from app.components.backend.api.auth.router import login as api_login
from app.components.backend.api.auth.router import register as api_register
from app.components.backend.api.deps import login_rate_limit, register_rate_limit
from app.components.web_frontend.overseer_access import LOGIN, overseer_gate
from app.components.web_frontend.rendering import templates, with_query
from app.components.web_frontend.routes import overseer as overseer_routes
from app.components.web_frontend.routes.partials import (
    overseer_ai as overseer_ai_partials,
)
from app.components.web_frontend.routes.partials import (
    overseer_auth as overseer_auth_partials,
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
from app.core.audit import AuditEmitter, get_audit
from app.core.formatting import site_path
from app.core.security import (
    SESSION_COOKIE_NAME,
    clear_refresh_cookie,
    clear_session_cookie,
)
from app.models.user import UserCreate
from app.services.auth.deps import get_refresh_service, get_user_service
from app.services.auth.refresh_tokens import RefreshService
from app.services.auth.service import session_id_of
from app.services.auth.users import UserService

# Every Overseer page, stream and action passes one gate (overseer_access).
router = APIRouter(dependencies=[Depends(overseer_gate)])


@router.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """There is no landing page; the app opens on Overview."""
    return RedirectResponse(url="/overview", status_code=303)


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request, next: str = "") -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="pages/auth/login.html",
        context={"next": site_path(next, "") if next else ""},
    )


@router.get(LOGIN, response_class=HTMLResponse, include_in_schema=False)
async def overseer_login_page(request: Request, next: str = "") -> HTMLResponse:
    """The server-rendered entry point for Overseer, back to ``next`` after."""
    return templates.TemplateResponse(
        request=request,
        name="pages/overseer/login.html",
        context={"next": site_path(next, "") if next else ""},
    )


# Overseer pages' dialogs and forms (htmx fragments), then its pages: a
# partial's path wins over the pages' catch-all routes.
router.include_router(overseer_auth_partials.router)
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


@router.post("/login", include_in_schema=False)
@router.post(LOGIN, include_in_schema=False)
async def login_submit(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next: Annotated[str, Form()] = "",
    user_service: UserService = Depends(get_user_service),
    refresh_service: RefreshService = Depends(get_refresh_service),
    audit: AuditEmitter = Depends(get_audit),
    _rate_limit: None = Depends(login_rate_limit),
) -> RedirectResponse:
    """Sign in from the page form.

    Delegates to the API's own login handler: lockout, failed-attempt
    tracking, audit events, token minting and cookie scoping live there, and
    a copy here would drift. The rate limiter is re-declared above so this
    path shares the API's bucket.

    The redirect is handed in as the ``response``, so the session and refresh
    cookies the API handler sets land on the 303 the browser follows.
    """
    overseer = request.url.path == LOGIN
    redirect = RedirectResponse(
        url=site_path(next, "/overseer" if overseer else "/"), status_code=303
    )
    try:
        await api_login(
            request=request,
            response=redirect,
            form_data=OAuth2PasswordRequestForm(username=email, password=password),
            user_service=user_service,
            refresh_service=refresh_service,
            audit=audit,
            _rate_limit=None,
        )
    except HTTPException as exc:
        # The API answers wrong-password and locked-account with the same 401
        # so neither reveals whether the email exists; keep that as one
        # message. Its 403 comes only after a correct password, for a
        # disabled account, so saying so reveals nothing new.
        reason = "disabled" if exc.status_code == 403 else "invalid"
        return RedirectResponse(
            url=with_query(
                LOGIN if overseer else "/login",
                error=reason,
                next=site_path(next, "") or None,
            ),
            status_code=303,
        )
    return redirect


@router.get("/register", response_class=HTMLResponse, include_in_schema=False)
async def register_page(request: Request, next: str = "") -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="pages/auth/register.html",
        context={"next": site_path(next, "") if next else ""},
    )


@router.post("/register", include_in_schema=False)
async def register_submit(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    full_name: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",
    user_service: UserService = Depends(get_user_service),
    refresh_service: RefreshService = Depends(get_refresh_service),
    audit: AuditEmitter = Depends(get_audit),
    _rate_limit: None = Depends(register_rate_limit),
) -> RedirectResponse:
    """Create an account from the page form.

    Same delegation as ``login_submit``: the API handler owns the closed-
    signup check, duplicate detection and cookies. Every dependency it
    declares must be threaded through here: an omitted one stays a bare
    ``Depends`` sentinel and fails the moment the handler touches it.
    """
    redirect = RedirectResponse(url=site_path(next, "/overseer"), status_code=303)
    try:
        await api_register(
            request=request,
            response=redirect,
            user_data=UserCreate(
                email=email, password=password, full_name=full_name or None
            ),
            user_service=user_service,
            refresh_service=refresh_service,
            audit=audit,
            _rate_limit=None,
        )
    except HTTPException as exc:
        if exc.status_code == 403:
            return RedirectResponse(url="/register?error=closed", status_code=303)
        if exc.status_code == 400:
            return RedirectResponse(url="/register?error=exists", status_code=303)
        return RedirectResponse(url="/register?error=invalid", status_code=303)
    return redirect


@router.get("/logout", include_in_schema=False)
async def logout(
    request: Request, refresh_service: RefreshService = Depends(get_refresh_service)
) -> RedirectResponse:
    """Sign out and land back on the public landing page.

    A GET so a plain link works. The session is revoked, ending its access
    tokens wherever they were copied, and both cookies are dropped.
    """
    if session_id := session_id_of(request.cookies.get(SESSION_COOKIE_NAME)):
        await refresh_service.revoke_family(session_id)
    redirect = RedirectResponse(url="/", status_code=303)
    clear_session_cookie(redirect)
    clear_refresh_cookie(redirect)
    return redirect
