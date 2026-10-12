---
name: protect-an-endpoint
description: Use when an API endpoint in this project must require an authenticated user or a specific role. Covers the auth dependencies, attaching them to a route, and the session and cookie rules.
---

# Protect an endpoint

Authentication is enforced with FastAPI dependencies from the auth service, not
by inspecting sessions inside handlers. Attaching the dependency is what makes a
route require a signed-in user; a route without it is public.

## When to use

Use when a route must require an authenticated user, or a user with a specific
role, before it runs.

Do NOT use to add the endpoint itself (see the `add-api-endpoint` skill) or to
change the auth service internals.

## Files that change

- `app/services/auth/deps.py`: the auth dependencies (the current-user
  dependency and the role dependency); reuse these rather than writing a check.
- `app/components/backend/api/`: the endpoint module whose route you protect.

## Procedure

1. Write the failing test first: assert the route returns 401 without
   credentials and succeeds with them. Confirm it fails for the right reason.
2. Import the current-user dependency from `app/services/auth/deps.py`.
3. Attach it to the route with `Depends(...)`, binding the authenticated user to
   a handler parameter.
4. For a role-restricted route, use the role dependency rather than inspecting
   the user object by hand.
5. Run the gates and fix anything red.

## Gates

- `make check`: lint, typecheck, and test.

## Pitfalls

- Checking the session or cookie by hand inside a handler duplicates the auth
  logic and drifts from it; depend on the auth dependency instead.
- Protection is per-route: a route without the dependency attached is public
  even when other routes in the same module are protected.
- Auth state travels in the session cookie set by the auth flow; do not read or
  set that cookie manually, and keep the handler logic independent of any
  particular frontend.
