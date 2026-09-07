"""Shared route auth detection.

One reliable signal for "does this route require authentication?": whether its
dependant tree carries any security scheme (OAuth2 / HTTP bearer / API key).
That catches every auth path uniformly — a direct ``Depends(get_current_user)``,
a ``require_role(...)`` factory, and router-level ``dependencies=[...]`` alike —
because they all ultimately pull a credential via a ``SecurityBase`` scheme.

Earlier detectors keyed off a single auth callable or matched dependency *names*,
which silently mislabel role-based and router-level auth as public (a route that
returns 401 showing "Auth: None"). The dashboard route table and the
``api-load-test`` route list both go through this helper so their Auth columns
agree and stay correct.
"""

from fastapi.routing import APIRoute


def route_requires_auth(route: APIRoute) -> bool:
    """True when the route is protected by any security scheme."""
    return _has_security_scheme(route.dependant)


def _has_security_scheme(dependant: object) -> bool:
    """Whether ``dependant`` or any sub-dependency carries a security scheme."""
    if getattr(dependant, "security_requirements", None):
        return True
    for sub in getattr(dependant, "dependencies", ()):
        if _has_security_scheme(sub):
            return True
    return False
