---
name: add-background-job
description: Use when adding background work that runs off the request path in this project. Covers defining a task for the arq worker and enqueuing it.
---

# Add background job

Background work runs on the arq worker, off the request path.
Tasks live under `app/components/worker/`; a request enqueues a task and returns
immediately instead of blocking on the work.

## When to use

Use when work is too slow to run inside a request, or should run independently
of the request that triggers it.

Do NOT use for periodic or scheduled work (see the `add-scheduled-job` skill),
or for work that must finish before the response is sent (keep that inline).

## Files that change

- `app/components/worker/tasks/`: task functions live here.
- `app/components/worker/queues/`: queue definitions that group and route tasks.

## Procedure

1. Write the failing test first for the task function's behavior, independent of
   the queue. Confirm it fails for the right reason.
2. Define the task function in `app/components/worker/tasks/`:
   an async function, registered in a queue module under
   `app/components/worker/queues/`; arq runs it from that queue's function list.
3. Enqueue the task from the request handler or service instead of running the
   work inline.
4. Keep the task idempotent and its arguments serializable.
5. Run the gates and fix anything red.

## Gates

- `make check`: lint, typecheck, and test.

## Pitfalls

- Task arguments cross a process boundary and must be serializable; passing a
  live object such as a database session or connection fails at enqueue or run
  time.
- The worker can retry a task, so a non-idempotent task corrupts state on
  retry; make each run safe to repeat.
- Running the slow work inline "just this once" defeats the purpose; enqueue it
  so the request returns promptly.
