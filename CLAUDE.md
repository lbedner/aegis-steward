# CLAUDE.md

Guidance for coding agents working in aegis-steward, an application
generated with Aegis Stack. This file loads every session; task procedures live
in `.claude/skills/` and load on demand.

## What this project is

aegis-steward (`aegis-steward`) is an Aegis Stack project: an async
Python application with a FastAPI backend, a full test suite, and Docker
containerization. Change the stack with the `aegis` CLI, never by hand.

Selected capabilities:

- Backend (FastAPI): the HTTP API and application wiring.
- Web frontend (htmx + Alpine.js + DaisyUI): server-rendered pages.
- Database (sqlite): SQLModel models and alembic migrations.
- Worker (arq): background job processing.
- Scheduler (sqlite): periodic and scheduled jobs.
- Redis: cache and message broker.
- Auth (basic): user accounts and protected endpoints.
- AI (pydantic-ai): chat and LLM capabilities.
- Finance: account and transaction tracking.
- Comms: email, SMS, and voice.
- Secrets: encrypted credential store; read keys with `await secrets.get(name)`.
- Deploy (compose): the socket-proxy service gives `app.core.runtime` read-only Docker access.
- Ingress: reverse proxy and TLS.

## Daily commands

- `make test` - run the test suite
- `make lint` - lint with ruff
- `make typecheck` - type-check
- `make check` - lint, typecheck, and test; run before considering work done
- `make serve` - run the stack locally

Every target above also runs as `uv run poe <target>` (`uv run poe test`,
`uv run poe check`, ...) for platforms without `make`, such as Windows, once
dev dependencies are installed (`uv sync`).

Host-side commands that talk to services resolve dynamic ports through
`.env.ports`; use the Makefile targets (or source that file) so the CLI reaches
the right port.

## Layout

- `app/components/` is infrastructure (backend, frontend, worker, scheduler,
  database, redis) and defines WHEN and WHERE.
- `app/services/` is business logic (auth, AI, and the rest) and defines WHAT.
- `tests/` mirrors the app layout: unit tests beside the code they cover, API
  tests under `tests/api/`.

## Changing the stack: use the CLI

Add or remove capabilities with the `aegis` CLI, never by hand-scaffolding:

- `aegis add <name>` - add a component or service
- `aegis remove <name>` - remove one
- `aegis update` - pull framework updates into this project

Hand-rolling a component or service leaves it unregistered and unmanaged; the
CLI wires routes, tests, health checks, and dependencies for you.

## Working rules

- TDD: write the failing test first, confirm it fails for the right reason, then
  implement.
- Types are mandatory: annotate every function's parameters and return type; use
  `str | None`, `dict[str, Any]`, `list[str]`.
- No emojis in code, docs, or output.
- Keep changes minimal and focused; do not scope-creep.
- DRY: before writing a new component, endpoint, or helper, search for an
  existing one that already does it (or most of it) and extend/reuse that
  instead of duplicating logic.
- Duplication is a bug, not a nice-to-have. Agents work safely only when
  each thing has one home: a change to a core thing cannot miss a second
  copy that does not exist. So any review finding of a second copy (markup,
  logic, a selector, an hx-attribute recipe, a label computed in both Python
  and JS) is fixed in the same session, never deferred or offered as
  optional. Logic shared with the browser is sent from the server; markup a
  script needs lives in a `<template>` in the partial, never in JS strings.

## Database

Models are SQLModel classes; every schema change goes through an alembic
migration. Never query inside a loop (N+1); batch with `WHERE id IN (...)` or
eager-load with `selectinload()`/`joinedload()`. `make check-queries` runs
the test suite under queryspy and fails on any repeated-query site that is
not already in `.queryspy-baseline.json` (CI's test job runs the same
flags); the baseline is the known debt, shrink it, never add to it. A
statement is known by its shape (its SQL with the selected columns collapsed,
`tests/_queryspy_shape.py`), so adding a column changes no entry.
`make check-queries-baseline` rewrites it after a fix. See the `add-model-and-migration` skill.

## Worker

Background jobs run on the arq backend. Enqueue long-running
work instead of blocking a request. See the `add-background-job` skill.

## Scheduler

Periodic and scheduled jobs run on the sqlite backend, and
execution history is recorded. See the `add-scheduled-job` skill.

## Authentication

Every route, socket and mount is behind one gate
(`app/components/backend/middleware/request_gate.py`): signed out, only its
public list answers, so a new route needs nothing to be protected. Never add
to that list beyond the way in. Admin-only work (sends, enqueue, run-now)
takes `require_admin`. Owner scope is `get_owner_user_id`: the household's
rows (`None`) for whoever is signed in. See the `protect-an-endpoint` skill.

## Skills

Task procedures live in `.claude/skills/<name>/SKILL.md` and load on demand when
the task matches. Reach for one when adding a PAGE, an API endpoint, a model, a
job, or a CLI command, or when changing the stack. Before planning any change to
the code, run it past the twin (the app's model of itself): the
`plan-with-the-twin` skill.

`add-page` is the one to read before touching the htmx frontend: the six
interaction patterns, the response contract, the render helper, the macro kit,
and the two-render-path test.
