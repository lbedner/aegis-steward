"""Authentication API routes."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm

from app.components.backend.api.auth import admin, sessions
from app.components.backend.api.auth.sessions import sign_in_metadata
from app.components.backend.api.deps import (
    login_rate_limit,
    register_rate_limit,
)
from app.components.backend.security.rate_limit import get_client_ip
from app.core.audit import AuditEmitter, get_audit
from app.core.config import settings
from app.core.security import (
    set_refresh_cookie,
    set_session_cookie,
    verify_password,
)
from app.core.time import utcnow
from app.models.user import (
    TokenResponse,
    UserCreate,
    UserResponse,
)
from app.services.auth.deps import (
    get_refresh_service,
    get_session_token,
    get_user_service,
)
from app.services.auth.refresh_tokens import RefreshService
from app.services.auth.service import get_current_user_from_token
from app.services.auth.users import UserService

router = APIRouter(prefix="/auth", tags=["authentication"])

# Sub-routers carry no prefix, tags or dependencies of their own:
# include_router hands them this router's, so every path stays what
# it was before the split.
router.include_router(sessions.router)
router.include_router(admin.router)


@router.post("/register", response_model=UserResponse)
async def register(
    request: Request,
    response: Response,
    user_data: UserCreate,
    user_service: UserService = Depends(get_user_service),
    refresh_service: RefreshService = Depends(get_refresh_service),
    audit: AuditEmitter = Depends(get_audit),
    _rate_limit: None = Depends(register_rate_limit),
):
    """Register a new user and sign them in via session cookie."""
    if not settings.REGISTRATION_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Registration is closed"
        )

    # Check if user already exists
    existing_user = await user_service.get_user_by_email(user_data.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered"
        )

    # Create new user
    user = await user_service.create_user(user_data)

    await audit.emit(
        "auth.user_registered",
        actor_email=user_data.email,
        target_type="user",
        target_id=user.id,
    )

    # Auto-sign-in: set the HttpOnly session cookie so the client lands on a
    # signed-in state without a second round-trip. The refresh token is an
    # opaque, server-side row; its cookie is path-scoped to ``/api/v1/auth``
    # so it only rides on auth endpoints.
    access_token, refresh_token = await refresh_service.start_session(
        user,
        source="password",
        **await sign_in_metadata(request, refresh_service),
    )
    set_session_cookie(response, access_token)
    set_refresh_cookie(response, refresh_token)

    return UserResponse.model_validate(user)


@router.post("/token", response_model=TokenResponse)
async def login(
    request: Request,
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    user_service: UserService = Depends(get_user_service),
    refresh_service: RefreshService = Depends(get_refresh_service),
    audit: AuditEmitter = Depends(get_audit),
    _rate_limit: None = Depends(login_rate_limit),
) -> TokenResponse:
    """Login and get access token.

    Sets the HttpOnly ``aegis_session`` cookie *and* returns the token in
    the JSON body. Browser-based callers rely on the cookie; pure API
    clients (CLIs, tests) keep using the bearer token from the body.
    """
    ip_address = get_client_ip(request)

    # Get user by email (username field in OAuth2 form)
    user = await user_service.get_user_by_email(form_data.username)

    # Locked accounts still emit an audit event but return the same
    # 401 "Incorrect email or password" response as the missing-user
    # path. A distinct 403 leaked email existence: hitting /token with
    # a random password ten times then comparing the 401 vs 403 split
    # let an attacker enumerate registered accounts.
    if user and await user_service.is_account_locked(user):
        await audit.emit("auth.login_locked", actor_email=form_data.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user or not verify_password(form_data.password, user.hashed_password):
        # Record the failed attempt if user exists
        if user:
            await user_service.record_failed_login(form_data.username)
        await audit.emit(
            "auth.login_failed", actor_email=form_data.username, ip_address=ip_address
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # A disabled account is refused here, after the password check, so only
    # someone who already knows the password learns that it is disabled.
    if not user.is_active:
        await audit.emit(
            "auth.login_disabled",
            actor_email=form_data.username,
            actor_id=user.id,
            ip_address=ip_address,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled"
        )

    # Successful login — reset failed attempts
    await user_service.reset_login_attempts(user.id)

    # Update last_login timestamp
    await user_service.update_user(user.id, last_login=utcnow())

    # See /register for the cookie scoping rationale.
    access_token, refresh_token = await refresh_service.start_session(
        user,
        source="password",
        **await sign_in_metadata(request, refresh_service),
    )
    set_session_cookie(response, access_token)
    set_refresh_cookie(response, refresh_token)

    await audit.emit(
        "auth.login_success",
        actor_email=form_data.username,
        actor_id=user.id,
        ip_address=ip_address,
    )

    return TokenResponse(access_token=access_token)


@router.get("/me", response_model=UserResponse)
async def get_current_user(
    token: str | None = Depends(get_session_token),
    user_service: UserService = Depends(get_user_service),
) -> UserResponse:
    """Get current authenticated user."""
    user = await get_current_user_from_token(token, user_service)
    return UserResponse.model_validate(user)
