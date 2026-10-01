"""A repeated statement is known debt by its shape, not its text.

queryspy names a repeated statement by its whole SQL, select list included,
so adding a column to a table renamed every SELECT on it: the baseline read
as all new, and the only way back was rewriting it, which hides whether
anything really changed.
"""

from queryspy import _baseline, pytest_plugin
from queryspy._baseline import BaselineEntry

from tests import _queryspy_shape
from tests._queryspy_shape import shaped, statement_shape

NARROW = "SELECT t.id, t.name FROM t WHERE t.owner_id = :owner_id_1"
WIDER = "SELECT t.id, t.name, t.deleted_at FROM t WHERE t.owner_id = :owner_id_1"
OTHER_WHERE = "SELECT t.id, t.name FROM t WHERE t.budget_id = :budget_id_1"


def _entry(label: str, kind: str = "repeated_statement") -> BaselineEntry:
    return BaselineEntry(kind=kind, label=label, file="app/x.py", function="read")


def test_a_new_column_keeps_the_shape() -> None:
    assert statement_shape(NARROW) == statement_shape(WIDER)
    assert shaped(_entry(NARROW)) == shaped(_entry(WIDER))


def test_a_different_query_is_a_different_shape() -> None:
    assert statement_shape(NARROW) != statement_shape(OTHER_WHERE)


def test_the_shape_still_reads_as_sql() -> None:
    assert statement_shape(WIDER) == "SELECT ... FROM t WHERE t.owner_id = :owner_id_1"


def test_only_statements_are_reshaped() -> None:
    """A lazy or column load is named by its model, which a column does
    not change."""
    column_load = _entry("Account", kind="column_load")

    assert shaped(column_load) == column_load


def test_queryspy_names_every_finding_by_its_shape() -> None:
    """Every comparison queryspy makes (split, stale, save) builds its
    entries through ``entry_for``; a queryspy that stops doing so, or
    moves the baseline stash, breaks this file and has to fail here."""
    assert _baseline.entry_for is _queryspy_shape.entry_for
    assert hasattr(pytest_plugin, "_BASELINE")
