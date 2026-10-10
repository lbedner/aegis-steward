"""Every migration must carry a stamp signature.

The startup hook re-adopts a persisted database by checking, per pending
migration, whether its signature object already exists - and stamping
instead of replaying the DDL. A migration that ships without a signature
opts out of that recovery: the day a volume outlives its version row, the
upgrade replays, and the database logs an "already exists" error on every
boot until someone stamps by hand. That is not hypothetical - 004..007
shipped unsigned and did exactly this.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re

import pytest

signatures_module = pytest.importorskip(
    "app.components.backend.startup.migration_signatures",
    reason="no database component in this stack",
)

VERSIONS = Path(__file__).parent.parent / "alembic" / "versions"


def _service_suffixes() -> list[str]:
    names = []
    for p in sorted(VERSIONS.glob("*.py")):
        m = re.match(r"\d+_(.+)", p.stem)
        if m:
            names.append(m.group(1))
    return names


def _revision_files() -> list[Path]:
    return [p for p in sorted(VERSIONS.glob("*.py")) if not p.name.startswith("__")]


def test_every_migration_file_has_a_stamp_signature() -> None:
    signatures = signatures_module.SERVICE_MIGRATION_SIGNATURES
    missing = []
    for path in sorted(VERSIONS.glob("*.py")):
        match = re.match(r"\d+_(.+)", path.stem)
        if match and match.group(1) not in signatures and not _carried_signature(path):
            missing.append(path.name)
    assert not missing, (
        f"migrations without a stamp signature: {missing} - add an entry to "
        "SERVICE_MIGRATION_SIGNATURES or declare aegis_stamp_signature"
    )


def _carried_signature(path: Path) -> tuple[str, ...] | None:
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "aegis_stamp_signature"
            for target in node.targets
        ):
            return tuple(ast.literal_eval(node.value))
    return None


def test_generated_signature_takes_precedence_over_legacy_service_entry() -> None:
    from types import SimpleNamespace

    from app.components.backend.startup.migrations import _signature_for_revision

    revision = SimpleNamespace(
        path="/tmp/038_ai.py",
        module=SimpleNamespace(
            aegis_stamp_signature=("column", "llm_usage", "duration_ms")
        ),
    )
    assert _signature_for_revision(revision, {"ai": ("table", "llm_org")}) == (
        "column",
        "llm_usage",
        "duration_ms",
    )


def test_signatures_name_real_model_objects() -> None:
    """A signature pointing at a table nobody declares can never fire."""
    sqlmodel = pytest.importorskip(
        "sqlmodel", reason="stack has migrations dir but no ORM (e.g. worker-only)"
    )

    from app.core.model_registry import import_all_models

    # Every table registers the way alembic's env.py registers them.
    import_all_models()

    tables = set(sqlmodel.SQLModel.metadata.tables)
    bare = {t.split(".")[-1] for t in tables}
    installed = set(_service_suffixes())
    for service, sig in signatures_module.SERVICE_MIGRATION_SIGNATURES.items():
        if service not in installed:
            continue  # signature for a service this stack doesn't ship
        name = sig[1]
        assert name in tables or name in bare or name.split(".")[-1] in bare, (
            f"signature for '{service}' names '{name}', which no model declares"
        )


@pytest.mark.skipif(
    not (VERSIONS.parent / "alembic.ini").exists(),
    reason="this stack ships no migrations",
)
def test_the_startup_hook_can_read_a_carried_signature() -> None:
    """Alembic hands the hook the revision module; the constant rides on it.

    The hook reads ``rev.module.aegis_stamp_signature`` inside a broad
    try/except, so a broken mechanism would go unnoticed at boot: this is
    what fails instead.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic/alembic.ini"))
    carried = {
        rev.revision: getattr(rev.module, "aegis_stamp_signature", None)
        for rev in script.walk_revisions()
    }
    on_disk = {
        path.stem: _carried_signature(path)
        for path in _revision_files()
        if _carried_signature(path) is not None
    }
    if not on_disk:
        pytest.skip("no generated revisions in this project")
    assert any(carried.values()), (
        "revisions declare aegis_stamp_signature on disk, but alembic exposes "
        f"none of them to the startup hook: {sorted(on_disk)}"
    )


def _create(table: str) -> object:
    import sqlalchemy as sa

    from alembic.operations import ops

    return ops.CreateTableOp(table, [sa.Column("id", sa.Integer, primary_key=True)])


@pytest.fixture
def gen() -> object:
    """The revision generator; a stack without a database renders a stub."""
    pytest.importorskip("alembic", reason="no database component in this stack")
    return pytest.importorskip("app.cli.migrate_gen")


def test_the_signature_is_the_services_own_table_never_a_swept_one(
    gen: object,
) -> None:
    """A project older than a component's own migration hands that
    component's tables to whichever service is added next (the sweep).
    Signed by one of those tables, the revision would be stamped as done
    on boot, because the table already exists, and its own tables never
    created."""
    kept = [_create("apscheduler_jobs"), _create("blog_post")]
    assert gen._signature(kept, own_tables={"blog_post"}) == ("table", "blog_post")


def test_a_revision_with_no_table_of_its_own_still_signs(gen: object) -> None:
    assert gen._signature([_create("apscheduler_jobs")], own_tables=set()) == (
        "table",
        "apscheduler_jobs",
    )
