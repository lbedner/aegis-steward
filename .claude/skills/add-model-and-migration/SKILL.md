---
name: add-model-and-migration
description: Use when adding or changing a database model in this project. Covers the SQLModel table definition, the alembic autogenerate migration flow, and the query rules that keep access performant.
---

# Add model and migration

Database tables are SQLModel classes, and every schema change is captured by an
alembic migration. The model and the migration are two separate artifacts:
changing the model without generating a migration drifts the running schema from
the code.

**THE MIGRATION IS WHAT CREATES THE TABLE. The server will not.** Startup runs
`alembic upgrade head` and nothing else; a model whose table no migration creates
is a startup error naming the table. It used to call `SQLModel.metadata.create_all`,
which reads as harmless and is how the version number falls behind: the moment a
new model became importable, the dev server's next reload built its table, and
alembic afterwards found it already there. That cost a hand-stamp twice
(`finance_icon`, then the insurance tables at 026). If your new table appears
without a migration, something is wrong, not convenient.

Two more rules that follow from it:

- **Give the migration a signature.** Add an entry to
  `app/components/backend/startup/migration_signatures.py` naming the object that
  proves it ran (a table, a column, an FK, a named CHECK). Startup uses it to
  *stamp* a database that already has the object instead of replaying the DDL.
  A migration with no signature stops the adoption walk, so every migration after
  it replays too.
- **`EXTERNALLY_OWNED` is not an escape hatch.** A table only belongs there when
  a library genuinely owns it (APScheduler's job store). Adding a name to quieten
  a failing startup defeats the check entirely.

## When to use

Use when adding a table, adding or changing a column, or adding an index.

Do NOT use for query-only changes that touch no schema (no migration needed), or
for non-database state.

## Files that change

- `app/services/`: models live in a service package as `models.py` (SQLModel
  classes with `table=True`).
- `alembic/env.py`: imports the models so autogenerate can see them; a model it
  cannot import is omitted from the migration.
- `alembic/versions/`: the generated revision lands here.

## Procedure

1. Write the failing test first (the query or behavior that needs the new
   column or table). Confirm it fails for the right reason.
2. Define or edit the SQLModel class in the service's `models.py`.
3. Make sure the model is imported where `alembic/env.py` collects metadata, so
   autogenerate sees it.
4. Generate the migration with alembic autogenerate, then open the new file in
   `alembic/versions/` and confirm it contains the intended change and nothing
   spurious.
5. Run the gates and fix anything red.

## Gates

- `make check`: lint, typecheck, and test.

## Pitfalls

- Never query inside a loop (N+1): batch with `WHERE id IN (...)` or eager-load
  relationships with `selectinload()` or `joinedload()`, or the query count
  grows with the row count.
- A model that `alembic/env.py` cannot import produces an empty or partial
  migration, because autogenerate only sees imported metadata.
- The SQLModel class and the migration are independent; editing one without the
  other leaves the schema and the code out of sync with no error until runtime.
