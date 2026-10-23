"""Administering other people's accounts.

Two complete variants: with RBAC every route is role-guarded, and
without it every route needs a signed-in user. The gate is the one that was already
here - the whole block moved with it.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.audit import AuditEmitter, get_audit
from app.models.user import (
    User,
    UserCreate,
    UserResponse,
)
from app.services.auth.deps import (
    get_current_active_user,
    get_session_token,
    get_user_service,
)
from app.services.auth.service import (
    RestoreConflictError,
    UserService,
    get_current_user_from_token,
)

router = APIRouter()

# Who may see, and who may change, other people's accounts. The guards are
# for routes. The ``may_*`` checks are for callers that already hold the
# user, like the web Overseer, which calls these handlers directly and so
# skips their Depends.
require_user_reader = get_current_active_user
require_user_admin = get_current_active_user


def may_read_users(user: User) -> bool:
    """Without roles, any signed-in user may."""
    return True


def may_admin_users(user: User) -> bool:
    """Without roles, any signed-in user may."""
    return True


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    current_user: User = Depends(require_user_reader),
    user_service: UserService = Depends(get_user_service),
) -> list[UserResponse]:
    """List all users. Requires a signed-in user."""
    users = await user_service.list_users()
    return [UserResponse.model_validate(user) for user in users]


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreate,
    current_user: User = Depends(require_user_admin),
    user_service: UserService = Depends(get_user_service),
    audit: AuditEmitter = Depends(get_audit),
) -> UserResponse:
    """Add an account for someone else.

    Unlike ``/register`` it signs nobody in and ignores
    ``REGISTRATION_ENABLED``: it is how accounts get made while sign-up is
    closed.
    """
    if await user_service.get_user_by_email(user_data.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered"
        )
    user = await user_service.create_user(user_data)
    await audit.emit(
        "auth.user_created",
        actor_id=current_user.id,
        target_type="user",
        target_id=user.id,
    )
    return UserResponse.model_validate(user)


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    token: str | None = Depends(get_session_token),
    user_service: UserService = Depends(get_user_service),
) -> UserResponse:
    """Get a specific user by ID. Caller must be the user or an admin."""
    current_user = await get_current_user_from_token(token, user_service)
    is_self = current_user.id == user_id

    is_admin = False

    if not is_self and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this user",
        )

    user = await user_service.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return UserResponse.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    full_name: str | None = None,
    token: str | None = Depends(get_session_token),
    user_service: UserService = Depends(get_user_service),
) -> UserResponse:
    """Update a user's profile. Caller must be the user or an admin."""
    current_user = await get_current_user_from_token(token, user_service)
    is_self = current_user.id == user_id

    is_admin = False

    if not is_self and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this user",
        )

    updates: dict[str, str] = {}
    if full_name is not None:
        updates["full_name"] = full_name

    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )

    user = await user_service.update_user(user_id, **updates)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return UserResponse.model_validate(user)


@router.patch("/users/{user_id}/deactivate", response_model=UserResponse)
async def deactivate_user(
    user_id: int,
    current_user: User = Depends(require_user_admin),
    user_service: UserService = Depends(get_user_service),
    audit: AuditEmitter = Depends(get_audit),
) -> UserResponse:
    """Deactivate a user account. Requires a signed-in user."""
    user = await user_service.deactivate_user(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    await audit.emit(
        "auth.user_deactivated",
        actor_id=current_user.id,
        target_type="user",
        target_id=user_id,
    )
    return UserResponse.model_validate(user)


@router.patch("/users/{user_id}/activate", response_model=UserResponse)
async def activate_user(
    user_id: int,
    current_user: User = Depends(require_user_admin),
    user_service: UserService = Depends(get_user_service),
    audit: AuditEmitter = Depends(get_audit),
) -> UserResponse:
    """Activate a user account. Requires a signed-in user."""
    user = await user_service.activate_user(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    await audit.emit(
        "auth.user_activated",
        actor_id=current_user.id,
        target_type="user",
        target_id=user_id,
    )
    return UserResponse.model_validate(user)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    hard: bool = False,
    current_user: User = Depends(require_user_admin),
    user_service: UserService = Depends(get_user_service),
    audit: AuditEmitter = Depends(get_audit),
) -> None:
    """Delete a user. Requires a signed-in user.

    Default is a soft delete (``deleted_at`` set, row preserved). Pass
    ``?hard=true`` for permanent removal.
    """
    if hard:
        success = await user_service.hard_delete_user(user_id)
    else:
        success = await user_service.delete_user(user_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    await audit.emit(
        "auth.user_hard_deleted" if hard else "auth.user_deleted",
        actor_id=current_user.id,
        target_type="user",
        target_id=user_id,
    )


@router.post("/users/{user_id}/restore", response_model=UserResponse)
async def restore_user(
    user_id: int,
    current_user: User = Depends(require_user_admin),
    user_service: UserService = Depends(get_user_service),
    audit: AuditEmitter = Depends(get_audit),
) -> UserResponse:
    """Clear ``deleted_at`` on a soft-deleted user. Requires a signed-in user.

    Returns 409 if another live user has claimed the same email since
    the soft delete.
    """
    try:
        restored = await user_service.restore_user(user_id)
    except RestoreConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    if restored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found or not deleted",
        )
    await audit.emit(
        "auth.user_restored",
        actor_id=current_user.id,
        target_type="user",
        target_id=user_id,
    )
    return UserResponse.model_validate(restored)
