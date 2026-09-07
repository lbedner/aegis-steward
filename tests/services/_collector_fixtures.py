"""Shared collector-test helpers.

In auth=on mode, every collector requires a ``Project`` to write rows
(``insight_metric.project_id`` is NOT NULL). Tests in this module seed
one project + user and pass it to collector constructors.

In auth=off mode, ``Project`` and ``User`` don't exist; the helper
returns ``None`` and tests pass no ``project=`` kwarg. The collectors
themselves handle ``project=None`` by falling back to env-var config.
"""

from __future__ import annotations

from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession


async def seed_project_for_collector(
    session: AsyncSession,
    *,
    slug: str = "test-collector-project",
    name: str = "Test Collector Project",
    github_owner: str | None = "lbedner",
    github_repo: str | None = "aegis-stack",
    github_token: str | None = None,
    pypi_package: str | None = "aegis-stack",
    plausible_site: str | None = "docs.example.com",
    plausible_api_key: str | None = None,
) -> Any | None:
    """Seed a User + Project for collector tests.

    Returns the Project on auth=on builds, ``None`` on auth=off (no
    Project model exists). Tests pass the result via
    ``**({"project": p} if p else {})`` so the same code path works in
    both modes.

    This helper constructs ``Project(...)`` directly; it does NOT go
    through ``ProjectService.create()``, which is the layer that
    encrypts credential fields (``github_token``, ``plausible_api_key``)
    at write time. Whatever string is passed for those fields lands in
    the DB unmodified — so callers should use dummy values like
    ``"github_pat_xxx"``, never real secrets.

    Each call uses a unique slug per default so two seed calls in the
    same test session don't collide on the (organization_id, slug)
    unique constraint — pass an explicit ``slug`` to control it.
    """
    try:
        from app.models.user import User
        from app.services.insights.models import Project
    except ImportError:
        return None

    user = User(
        email=f"collector-test-{slug}@example.com",
        full_name="Collector Test User",
        hashed_password="x",
    )
    session.add(user)
    await session.flush()

    # Org + owner membership — required since the per_user variant
    # ships ``project.organization_id NOT NULL``.
    from tests._test_org import ensure_org_for_user

    org_id = await ensure_org_for_user(session, user)

    project = Project(
        slug=slug,
        name=name,
        owner_user_id=user.id,  # type: ignore[arg-type]
        organization_id=org_id,
        github_owner=github_owner,
        github_repo=github_repo,
        github_token=github_token,
        pypi_package=pypi_package,
        plausible_site=plausible_site,
        plausible_api_key=plausible_api_key,
    )
    session.add(project)
    await session.flush()
    return project


def collector_kwargs(project: Any | None) -> dict[str, Any]:
    """Build the keyword args for a collector constructor.

    Returns ``{"project": project}`` on auth=on, ``{}`` on auth=off.
    Use as ``Collector(db, **collector_kwargs(project))``.
    """
    return {"project": project} if project is not None else {}
