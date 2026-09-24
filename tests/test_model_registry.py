"""The model registry finds every table this project defines.

``import_all_models()`` walks ``app/models/`` and ``app/services/*/models``.
A table defined anywhere else never reaches ``SQLModel.metadata``, so alembic
autogenerate, ``migrate-fix`` and the startup re-adoption check are blind to
it. This test walks the source tree the slow way and compares.
"""

from __future__ import annotations

from pathlib import Path
import re
import sys

import pytest

pytest.importorskip("sqlmodel", reason="no database in this stack")

from app.core.model_registry import import_all_models  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "app"
TABLE_RE = re.compile(r"^class \w+\([^)]*\btable=True", re.M)


def _table_modules() -> set[str]:
    found = set()
    for path in APP.rglob("*.py"):
        if TABLE_RE.search(path.read_text()):
            rel = path.relative_to(APP.parent).with_suffix("").as_posix()
            found.add(rel.replace("/", ".").removesuffix(".__init__"))
    return found


def test_registry_imports_every_table_module() -> None:
    import_all_models()
    missing = sorted(m for m in _table_modules() if m not in sys.modules)
    assert not missing, "tables the registry never imported:\n  " + "\n  ".join(missing)


@pytest.mark.skipif(
    not (APP.parent / "alembic" / "alembic.ini").exists(),
    reason="this stack ships no migrations",
)
def test_generated_revisions_rebuild_the_models(tmp_path: Path) -> None:
    """New migrations must not add to steward's recorded legacy schema drift."""
    from app.cli.migrate_drift import drift

    known = set(
        (Path(__file__).parent / "known_schema_drift.txt").read_text().splitlines()
    )
    assert set(drift(scratch_dir=tmp_path)) - known == set()
