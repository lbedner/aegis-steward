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
- AI (pydantic-ai): chat and LLM capabilities.
- Finance: account and transaction tracking.
- Comms: email, SMS, and voice.

## Daily commands

- `make test` - run the test suite
- `make lint` - lint with ruff
- `make typecheck` - type-check
- `make check` - lint, typecheck, and test; run before considering work done
- `make serve` - run the stack locally

Every target above also runs as `uv run poe <target>` (`uv run poe test`,
`uv run poe check`, ...) for platforms without `make`, such as Windows, once
dev dependencies are installed (`uv sync --all-extras`).

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

## Database

Models are SQLModel classes; every schema change goes through an alembic
migration. Never query inside a loop (N+1); batch with `WHERE id IN (...)` or
eager-load with `selectinload()`/`joinedload()`. See the
`add-model-and-migration` skill.

## Worker

Background jobs run on the arq backend. Enqueue long-running
work instead of blocking a request. See the `add-background-job` skill.

## Scheduler

Periodic and scheduled jobs run on the sqlite backend, and
execution history is recorded. See the `add-scheduled-job` skill.

## Skills

Task procedures live in `.claude/skills/<name>/SKILL.md` and load on demand when
the task matches. Reach for one when adding an API endpoint, a model, a job, or
a CLI command, or when changing the stack.
