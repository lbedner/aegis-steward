---
name: change-the-stack
description: Use when adding or removing a capability (a component or service) in this project, or pulling framework updates. Covers the aegis add, remove, and update commands and what each one changes.
---

# Change the stack

Capabilities are managed by the `aegis` CLI, not by hand. The CLI wires routes,
tests, health checks, dependencies, and dashboard entries as a set; hand-adding
files leaves a capability half-registered and unmanaged.

## When to use

Use when adding a component (database, worker, scheduler, redis) or a service
(auth, AI, and the rest), removing one, or updating the project to a newer
framework version.

Do NOT use to change code inside a capability that already exists; that is
ordinary feature work (see the endpoint, model, job, and CLI skills).

## Files that change

The CLI owns the file changes. You run a command; it edits the registry-managed
files for you. Do not pre-create or delete capability files by hand.

## Procedure

1. Add a capability: `aegis add <name>` (for example `aegis add worker`). The
   CLI pulls in required dependencies and wires the capability.
2. Remove a capability: `aegis remove <name>`. The CLI deletes the files that
   capability owns and unwires it.
3. Update the project: `aegis update`. This regenerates the framework-managed
   shared files against the current template, keeping a backup of each file it
   overwrites so local edits can be recovered from the backup.
4. After any of these, run the gates and review the diff before relying on it.

## Gates

- `make check`: lint, typecheck, and test.

## Pitfalls

- Hand-scaffolding a component or service leaves it unregistered: routes, health
  checks, and dependencies are not wired, so use `aegis add` instead.
- `aegis update` overwrites framework-managed shared files; it backs them up
  first, but review the diff so intended local edits are preserved from the
  backup.
- Removing a capability deletes the files it owns; commit or stash unsaved work
  in those areas before running `aegis remove`.
