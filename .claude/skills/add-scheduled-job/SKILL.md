---
name: add-scheduled-job
description: Use when adding a job that runs on a schedule (periodic or at a fixed time) in this project. Covers writing the task function and registering it with the scheduler.
---

# Add scheduled job

Scheduled work runs under the scheduler component. Jobs are registered
explicitly in the scheduler entry point by importing a function and calling
`scheduler.add_job(...)`, so a schedule is not active until the job is
registered there.

## When to use

Use when work must run periodically or at a fixed time (a cron-like schedule).

Do NOT use for work triggered by a request (see the `add-background-job` skill
if a worker is present, or `add-api-endpoint` for a synchronous route).

## Files that change

- `app/components/scheduler/main.py`: the scheduler entry point; import the task
  function and register it with `scheduler.add_job(...)` and a trigger.
- The task function itself lives with the service or module it belongs to, not
  in the scheduler entry point.

## Procedure

1. Write the failing test first for the task function's behavior, independent of
   the schedule. Confirm it fails for the right reason.
2. Write the task function as a plain callable that does one unit of work.
3. In `app/components/scheduler/main.py`, import the function and register it
   with `scheduler.add_job(...)`, giving it an interval or a fixed-time trigger.
4. Keep the task idempotent: a schedule can fire late or twice, so a run must be
   safe to repeat.
5. Run the gates and fix anything red.

## Gates

- `make check`: lint, typecheck, and test.

## Pitfalls

- A task function that is never registered with `scheduler.add_job(...)` never
  runs; registration in the entry point is what activates the schedule.
- Scheduled runs can overlap or retry, so a non-idempotent task corrupts state;
  make each run safe to repeat.
- Long-running work inside a scheduled task blocks the next run; hand heavy work
  to a background job instead when a worker is present.
