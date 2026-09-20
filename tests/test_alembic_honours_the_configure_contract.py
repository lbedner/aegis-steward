"""A caller must be able to configure the migration context.

``app.cli.migrate_gen`` passes ``include_object``, ``compare_type`` and
``render_as_batch`` through ``config.attributes["configure"]``. That is
the contract; ``alembic/env.py`` is the half that has to honour it.

It did not. ``env.py`` called ``context.configure(...)`` with a fixed
keyword list and never looked at ``config.attributes``, so every option
a caller set was silently dropped - ``render_as_batch`` among them.
Without batch mode, autogenerate emits
``ALTER TABLE x ALTER COLUMN y DROP NOT NULL``, which SQLite has no
syntax for, and every generated revision is unusable. Found adding the
auth service on 2026-09-20: the generator wrote a revision, then failed
replaying its own output.

Silent is the problem. A dropped option looks exactly like an option
nobody set, so the failure surfaces much later as invalid SQL in a
migration nobody hand-wrote.
"""

from __future__ import annotations

import ast
from pathlib import Path

ENV = Path(__file__).resolve().parents[1] / "alembic" / "env.py"


def _configure_calls(tree: ast.Module) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "configure"
    ]


def test_the_online_path_merges_what_a_caller_asked_for() -> None:
    source = ENV.read_text()
    assert 'config.attributes.get("configure"' in source, (
        "env.py ignores config.attributes['configure'], so migrate_gen's "
        "render_as_batch never reaches alembic"
    )


def test_options_are_not_passed_as_a_second_keyword() -> None:
    """Defaults belong in the merged dict, never as their own keyword.

    ``context.configure(connection=c, target_metadata=m, **options)``
    raises ``TypeError: got multiple values for keyword argument`` the
    moment a caller overrides the same option - which is exactly when it
    matters and exactly when nobody is testing.
    """
    tree = ast.parse(ENV.read_text())
    for call in _configure_calls(tree):
        if not any(kw.arg is None for kw in call.keywords):
            continue  # not the **options call
        named = {kw.arg for kw in call.keywords if kw.arg is not None}
        assert named <= {"connection", "url"}, (
            f"these are passed alongside **options and will collide: "
            f"{sorted(named - {'connection', 'url'})}"
        )
