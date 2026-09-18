"""Migrations create tables. The server does not.

``startup_database_init`` ran ``SQLModel.metadata.create_all`` over every
imported model, so the moment a new model became importable the dev
server's reload created its table before any migration existed. Alembic
then found the table already there and the version lagged behind
(2026-09-17: ``insurance_policy`` and ``insurance_claim`` built by
create_all, stamped by hand at 026). ``_check_schema_mismatch`` runs
AFTER create_all, so it could never see the case it exists for.

The rule: when ``alembic/versions`` exists, the migrations are what
build the schema, and a model whose table no migration creates is a
startup error naming the table rather than a table conjured on the way
past.
"""

from pathlib import Path
import re

import pytest


def test_the_app_never_creates_tables_on_the_way_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Behaviour, not a source scan: on an install that HAS migrations,
    bringing the schema up must never call ``create_all``.

    One create_all survives, for a project generated without migrations
    at all - there is nothing else to build its tables. This proves that
    branch is not the one taken here.
    """
    from sqlmodel import SQLModel

    from app.components.backend.startup import database_init

    called: list[str] = []
    monkeypatch.setattr(
        SQLModel.metadata,
        "create_all",
        lambda *a, **k: called.append("create_all"),
    )

    db = tmp_path / "startup.db"
    monkeypatch.setattr(database_init, "_check_schema_mismatch", lambda: None)
    import app.core.db as db_module

    monkeypatch.setattr(db_module, "DATABASE_PATH", str(db))
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{db}")
    monkeypatch.setattr(db_module, "engine", engine)
    try:
        database_init._build_schema()
    finally:
        engine.dispose()

    assert called == [], "startup built tables instead of migrating"


def test_a_model_with_no_migration_stops_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The signal that never existed. create_all made the table and the
    version silently fell behind; now it is a startup error naming the
    table."""
    from app.components.backend.startup import database_init

    monkeypatch.setattr(database_init, "_check_schema_mismatch", lambda: None)
    import app.core.db as db_module

    db = tmp_path / "startup.db"
    monkeypatch.setattr(db_module, "DATABASE_PATH", str(db))
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{db}")
    monkeypatch.setattr(db_module, "engine", engine)

    import app.components.backend.startup.migrations as migrations

    monkeypatch.setattr(
        migrations, "missing_model_tables", lambda *a, **k: {"invented_table"}
    )
    try:
        with pytest.raises(RuntimeError, match="invented_table"):
            database_init._build_schema()
    finally:
        engine.dispose()


class TestAModelWithNoMigration:
    def test_it_is_named_rather_than_silently_created(self) -> None:
        """The failure that cost a hand-stamp: a table the code expects
        and no migration creates must stop startup and SAY WHICH."""
        from app.components.backend.startup.migrations import missing_model_tables

        class FakeInspector:
            def get_table_names(self, schema: str | None = None) -> list[str]:
                return ["party", "alembic_version"]

        missing = missing_model_tables(
            FakeInspector(), expected={"party", "insurance_policy", "matter"}
        )
        assert missing == {"insurance_policy", "matter"}

    def test_nothing_missing_is_an_empty_set(self) -> None:
        from app.components.backend.startup.migrations import missing_model_tables

        class FakeInspector:
            def get_table_names(self, schema: str | None = None) -> list[str]:
                return ["party", "matter"]

        assert missing_model_tables(FakeInspector(), expected={"party"}) == set()


class TestAdoptingAPersistedDatabase:
    """A database that already HAS the objects a pending migration would
    create is stamped, not replayed. Without that, alembic_version lags,
    the upgrade re-runs the DDL and the database logs "already exists"
    on every boot until somebody stamps by hand - the finance_icon
    incident, and then the insurance one."""

    def test_a_migration_whose_object_exists_is_adoptable(self) -> None:
        from app.components.backend.startup.migrations import already_applied

        class FakeInspector:
            def get_table_names(self, schema: str | None = None) -> list[str]:
                return ["insurance_policy", "agent"]

            def get_columns(self, table: str, schema: str | None = None):  # noqa: ANN202
                return [{"name": "id"}, {"name": "code_mode"}]

        assert already_applied(FakeInspector(), ("table", "insurance_policy")) is True
        assert already_applied(FakeInspector(), ("table", "nothing_here")) is False
        assert (
            already_applied(FakeInspector(), ("column", "agent", "code_mode")) is True
        )
        assert already_applied(FakeInspector(), ("column", "agent", "gone")) is False

    def test_a_signature_shape_nobody_defined_is_not_adopted(self) -> None:
        """Unknown means "cannot prove it ran", which must read as NOT
        applied: replaying DDL is noisy, stamping a migration that never
        ran leaves the schema short a table."""
        from app.components.backend.startup.migrations import already_applied

        class FakeInspector:
            def get_table_names(self, schema: str | None = None) -> list[str]:
                return []

        assert already_applied(FakeInspector(), ("sorcery", "x")) is False

    def test_every_shipped_migration_can_prove_itself(self) -> None:
        """A migration with no signature opts out of adoption silently,
        which is how the lag starts. Guarded here as well as in
        test_migration_signatures so the startup path owns the rule it
        depends on."""
        from app.components.backend.startup.migration_signatures import (
            SERVICE_MIGRATION_SIGNATURES,
        )

        versions = Path("alembic/versions")
        named = {
            match.group(1)
            for path in versions.glob("*.py")
            if (match := re.match(r"\d+_(.+)\.py$", path.name))
        }
        unsigned = sorted(named - set(SERVICE_MIGRATION_SIGNATURES))
        assert not unsigned, (
            "these migrations cannot prove they ran, so a persisted "
            f"database cannot adopt them: {unsigned}"
        )


@pytest.mark.asyncio
async def test_a_fresh_database_comes_up_at_head(tmp_path: Path) -> None:
    """End to end on a real file: no create_all anywhere, and the
    version table says head rather than lagging behind it."""
    from app.components.backend.startup.migrations import upgrade_to_head

    db = tmp_path / "fresh.db"
    upgrade_to_head(str(db))

    import sqlite3

    connection = sqlite3.connect(db)
    try:
        stamped = connection.execute(
            "select version_num from alembic_version"
        ).fetchone()
        tables = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='table'"
            )
        }
    finally:
        connection.close()

    assert stamped is not None, "a fresh database came up unstamped"
    # A table from an early migration and one from the latest: proof the
    # whole chain ran rather than just the first revision.
    assert "llm_org" in tables
    assert "insurance_policy" in tables
    # And the one this ticket's own check turned up: a model that had
    # been relying on create_all since before anybody noticed.
    assert "job_execution" in tables


class TestAdoptionStopsAtAGap:
    """Stamping to head because an EARLIER migration was adoptable
    would skip a later one whose DDL never ran, leaving the schema short
    a table and marked complete. That is the exact failure this module
    exists to prevent, so the walk stops at the first migration that
    cannot prove itself."""

    def test_it_walks_forward_from_the_current_revision(self) -> None:
        from app.components.backend.startup.migrations import _pending

        class Rev:
            def __init__(self, revision: str) -> None:
                self.revision = revision

        class FakeScript:
            def walk_revisions(self, base: str, heads: str):  # noqa: ANN202
                # alembic answers newest-first.
                return [Rev("003"), Rev("002"), Rev("001")]

        # From 001, only what comes after it, oldest first.
        assert [r.revision for r in _pending(FakeScript(), "001")] == ["002", "003"]
        # From nothing, everything, oldest first.
        assert [r.revision for r in _pending(FakeScript(), None)] == [
            "001",
            "002",
            "003",
        ]
        # A revision this install has never heard of: replay everything
        # rather than guess where to resume.
        assert len(_pending(FakeScript(), "beef")) == 3

    def test_a_migration_with_no_signature_stops_the_walk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A migration that cannot prove it ran must not be stamped
        over, even when the ones before it could be."""
        import app.components.backend.startup.migrations as migrations

        stamped: list[str] = []

        class Rev:
            def __init__(self, revision: str, path: str) -> None:
                self.revision = revision
                self.path = path

        class FakeScript:
            def walk_revisions(self, base: str, heads: str):  # noqa: ANN202
                return [
                    Rev("003", "/a/003_unsigned.py"),
                    Rev("002", "/a/002_party.py"),
                    Rev("001", "/a/001_documents.py"),
                ]

        monkeypatch.setattr(
            migrations, "_pending", lambda script, current: FakeScript().walk_revisions(
                "base", "heads"
            )[::-1]
        )
        monkeypatch.setattr(migrations, "already_applied", lambda i, s: True)

        import alembic.command as command

        monkeypatch.setattr(command, "stamp", lambda cfg, rev: stamped.append(rev))

        db = tmp_path / "persisted.db"
        import sqlite3

        con = sqlite3.connect(db)
        con.execute("create table document (id integer primary key)")
        con.execute("create table alembic_version (version_num varchar)")
        con.execute("insert into alembic_version values ('001')")
        con.commit()
        con.close()

        adopted = migrations.adopt_pending(str(db))

        # 001 and 002 prove themselves; 003 has no signature, so the walk
        # STOPS there and the stamp goes to 002. Never to head - that is
        # the whole point: 003's DDL has not run, and stamping past it
        # would mark the schema complete while a table is missing.
        assert adopted == ["documents", "party"]
        assert stamped == ["002"]
