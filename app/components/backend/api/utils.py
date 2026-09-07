"""Shared API utilities for common error patterns."""

from fastapi import HTTPException, status


def raise_not_found(resource: str) -> None:
    """Raise 404 for a missing resource."""
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"{resource} not found",
    )


def raise_bad_request(detail: str) -> None:
    """Raise 400 with a detail message."""
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=detail,
    )


def validate_role(role: str, valid_roles: set[str]) -> None:
    """Validate a role is in the allowed set. Raises 400 if not."""
    if role not in valid_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role: {role}. Valid: {sorted(valid_roles)}",
        )
