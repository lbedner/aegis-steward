---
name: add-api-endpoint
description: Use when adding a new HTTP endpoint to this project's FastAPI backend. Covers the router module, request and response schemas, dependency wiring, router registration, the health surface, and the API test.
---

# Add API endpoint

Add an HTTP route to the FastAPI backend. Endpoints live under
`app/components/backend/api/` and are mounted by a single registration file, so
a route is not reachable until it is both defined and registered.

## When to use

Use when adding or changing an HTTP route the backend serves.

Do NOT use for background work (that is a job, not a request) or for scheduled
work. Do NOT hand-add a whole new service or component this way; use the `aegis`
CLI for that.

## Files that change

- `app/components/backend/api/`: endpoint modules live here. Add `<name>.py`
  with an `APIRouter`, or a `<name>/router.py` package for a group of routes.
- `app/components/backend/api/routing.py`: register the router here (import it
  and mount it with `app.include_router(...)`).
- `app/components/backend/api/deps.py`: shared FastAPI dependencies; add one
  here if the endpoint needs a reusable dependency.
- `app/components/backend/api/health.py`: the health surface; wire a check if
  the endpoint fronts a new subsystem.
- `tests/api/`: add `test_<name>_endpoints.py`.

## Procedure

1. Write the failing test first in `tests/api/test_<name>_endpoints.py`, driving
   the route through the test client. Confirm it fails for the right reason (the
   route does not exist yet).
2. Define the request and response schemas as Pydantic or SQLModel classes.
3. Create the router module in `app/components/backend/api/` with an
   `APIRouter()` and the route handlers as async `def`.
4. Register the router in `app/components/backend/api/routing.py`: import it and
   call `app.include_router(...)`.
5. Add any reusable dependency to `app/components/backend/api/deps.py`, and wire
   a health check in `health.py` if the endpoint fronts a new subsystem.
6. Run the gates and fix anything red.

## Gates

- `make check`: lint, typecheck, and test.

## Pitfalls

- A router that is defined but never registered in `routing.py` returns 404; the
  registration step is what makes the route reachable.
- Route handlers are async `def`; a blocking call inside one stalls the event
  loop and slows every request, so use the async client or run blocking work off
  the loop.
- Returning a raw dict instead of the declared response model skips validation
  and drifts the response from its schema.
