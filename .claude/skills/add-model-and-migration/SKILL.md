---
name: add-model-and-migration
description: Use when adding or changing a database model in this project. Covers the SQLModel table definition, the alembic autogenerate migration flow, and the query rules that keep access performant.
---

# Add model and migration

Database tables are SQLModel classes, and every schema change is captured by an
alembic migration. The model and the migration are two separate artifacts:
changing the model without generating a migration drifts the running schema from
the code.

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
