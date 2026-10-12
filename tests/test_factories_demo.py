"""
Live examples of the composable test-data factories.

The factories themselves live in ``tests/conftest.py`` so pytest
auto-discovers them. This file demonstrates the pattern so future
contributors (and the AI agents helping them) have a single place to
point at and say "this is how we set up test data."

The pattern is:

- ``user_factory`` / ``org_factory`` return a callable. Call it to
  produce a distinct row. Defaults guarantee uniqueness; pass kwargs
  to pin specific fields.
- Compose factories by taking another factory as a fixture argument
  (see ``owner_with_org`` in ``conftest.py``).
- Use a factory when the data setup is plumbing. Stay inline when the
  data shape is the point of the test.
"""

from fastapi import status
from fastapi.testclient import TestClient
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.user import User


class TestUserFactory:
    """``user_factory`` examples."""

    @pytest.mark.asyncio
    async def test_default_call_gives_unique_user(
        self, user_factory, async_db_session: AsyncSession
    ) -> None:
        """One call returns one persisted user with a unique email."""
        user = await user_factory()
        assert isinstance(user, User)
        assert user.id is not None
        assert user.email.endswith("@test.com")

    @pytest.mark.asyncio
    async def test_repeated_calls_produce_distinct_users(self, user_factory) -> None:
        """No collisions on repeated calls — the factory auto-uniques the email."""
        a = await user_factory()
        b = await user_factory()
        c = await user_factory()
        assert len({a.id, b.id, c.id}) == 3
        assert len({a.email, b.email, c.email}) == 3

    @pytest.mark.asyncio
    async def test_overrides_pin_specific_fields(self, user_factory) -> None:
        """Kwargs override the auto-generated defaults."""
        user = await user_factory(email="exact@example.com", full_name="Pinned Name")
        assert user.email == "exact@example.com"
        assert user.full_name == "Pinned Name"


class TestAuthenticatedClient:
    """``authenticated_client`` examples.

    The fixture wraps ``async_client_with_db`` and pre-injects the
    ``auth_headers`` bearer token, so test bodies stop repeating
    ``headers={"Authorization": ...}`` on every request.
    """

    @pytest.mark.asyncio
    async def test_protected_endpoint_just_works(
        self, authenticated_client: TestClient
    ) -> None:
        """``GET /auth/me`` returns 200 with no manual header plumbing."""
        response = authenticated_client.get("/api/v1/auth/me")
        assert response.status_code == status.HTTP_200_OK
        # Response body is the seeded ``auth_user`` / ``admin_user``.
        assert "email" in response.json()

    @pytest.mark.asyncio
    async def test_inline_headers_still_work_when_needed(
        self,
        async_client_with_db: TestClient,
        user_factory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When a test needs a *different* user than the seeded one,
        skip ``authenticated_client`` and mint a token inline (auth on:
        the suite signs in as the dev user otherwise)."""
        from app.core.config import settings
        from app.core.security import create_access_token

        monkeypatch.setattr(settings, "AUTH_ENABLED", True)

        other = await user_factory()
        token = create_access_token(data={"sub": other.email})

        response = async_client_with_db.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["email"] == other.email
