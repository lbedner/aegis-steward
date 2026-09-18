"""Schema-level reading for startup database init.

``_existing_tables_by_schema`` reads what actually exists, keyed to match
``SQLModel.metadata``.

There used to be a ``_create_missing_tables`` here, a safety net that
recreated any model table a stamp had marked applied without running. It
had no callers, and it contradicts the rule startup now follows: a model
table no migration creates is a failure to NAME, not one to paper over
by building the table on the way past. See
``startup/migrations.missing_model_tables``.
"""

from __future__ import annotations

from typing import Any


def _existing_tables_by_schema(inspector: Any) -> set[str]:
    """Existing tables across every schema our models use, keyed to match
    ``SQLModel.metadata.tables``: schema-qualified (``schema.name``) for
    non-default schemas, bare otherwise. Without the qualification a table
    in a component schema (e.g. ``scheduler``) reads as missing.
    """
    from sqlmodel import SQLModel

    model_schemas = {table.schema for table in SQLModel.metadata.tables.values()}
    existing: set[str] = set()
    for schema in model_schemas:
        for name in inspector.get_table_names(schema=schema):
            existing.add(f"{schema}.{name}" if schema else name)
    return existing
