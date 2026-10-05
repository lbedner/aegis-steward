"""A column the database requires, the model still writes.

A migration that creates a column ``nullable=False`` with only a Python
``default=`` gives the DATABASE no default: the insert must name a
value, and only the model names values. Take that column off the model
and every insert on a migrated database fails - while the tests, whose
schema is built from the models, never see the column at all.

That is how ``finance_valuation.is_stale`` broke every valuation from
2026-09-13 until the weekly envelope credit job said so (#386): the
model dropped it, the column stayed (a drop cannot be proven by a stamp
signature), and nothing could write a valuation.

So: read what the migrations leave NOT NULL without a server default,
and hold every such column to still be on its model.
"""

from __future__ import annotations

import ast
from pathlib import Path

from sqlmodel import SQLModel

from app.core.model_registry import import_all_models

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _required(column: ast.Call) -> str | None:
    """The column's name, if it is NOT NULL with no database default."""
    if not (column.args and isinstance(column.args[0], ast.Constant)):
        return None
    keywords = {kw.arg: kw.value for kw in column.keywords}
    nullable = keywords.get("nullable")
    if not (isinstance(nullable, ast.Constant) and nullable.value is False):
        return None
    if "server_default" in keywords or "primary_key" in keywords:
        return None
    return str(column.args[0].value)


def _is_column(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "Column"


def _text(node: ast.AST) -> str | None:
    return str(node.value) if isinstance(node, ast.Constant) else None


def _required_by_migrations() -> set[tuple[str, str]]:
    """``(table, column)`` the migrations leave required, in order."""
    required: set[tuple[str, str]] = set()
    for path in sorted(VERSIONS.glob("*.py")):
        tree = ast.parse(path.read_text())
        batch_table: dict[str, str] = {}  # ``with ... as batch_op`` -> table
        for node in ast.walk(tree):
            if isinstance(node, ast.With):
                for item in node.items:
                    call = item.context_expr
                    if (
                        isinstance(call, ast.Call)
                        and getattr(call.func, "attr", "") == "batch_alter_table"
                        and isinstance(item.optional_vars, ast.Name)
                        and (table := _text(call.args[0]))
                    ):
                        batch_table[item.optional_vars.id] = table
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            verb = getattr(node.func, "attr", "")
            owner = getattr(node.func, "value", None)
            on = batch_table.get(owner.id) if isinstance(owner, ast.Name) else None
            args = node.args if on is None else [ast.Constant(on), *node.args]
            if verb == "create_table" and (table := _text(args[0])):
                for column in filter(_is_column, args[1:]):
                    if name := _required(column):
                        required.add((table, name))
            elif verb == "add_column" and len(args) > 1 and _is_column(args[1]):
                if (table := _text(args[0])) and (name := _required(args[1])):
                    required.add((table, name))
            elif verb in ("drop_column", "alter_column") and len(args) > 1:
                # Dropped, or altered (nullable, a default): no longer ours
                # to police.
                required.discard((_text(args[0]) or "", _text(args[1]) or ""))
    return required


def test_every_column_the_database_requires_is_still_on_its_model() -> None:
    import_all_models()
    modelled = {
        table.name: set(table.columns.keys())
        for table in SQLModel.metadata.tables.values()
    }
    missing = sorted(
        f"{table}.{column}"
        for table, column in _required_by_migrations()
        if table.rpartition(".")[2] in modelled
        and column not in modelled[table.rpartition(".")[2]]
    )
    assert not missing, (
        "NOT NULL with no server default, and no longer on the model - "
        f"every insert into these tables fails: {missing}"
    )
