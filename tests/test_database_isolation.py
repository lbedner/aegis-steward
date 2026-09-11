"""The suite must not be able to touch the developer's real database.

``_no_production_database`` redirects the two session factories, which
keeps test ROWS out of the live file - the docstring there records the
incident that earned it, hundreds of "hello" conversations written into
a developer's ledger.

It did not redirect the ENGINE, and DDL goes through the engine.
``init_database()`` calls ``SQLModel.metadata.create_all(engine)``, and
``ConversationStore.__init__`` calls ``init_database()``, so merely
constructing one during the suite created every missing model table in
the file named by ``DATABASE_URL``.

That is not a data-loss bug - ``create_all`` only ever ADDS tables, and
never alters or drops one. It is worse in a quieter way: the table it
adds is frozen at whatever shape the models had at that moment, and
nothing ever corrects it. Found 2026-09-10, when a `make check` run
created ``finance_merchant_alias`` in the live ledger without the
``is_ambiguous`` column that was added minutes later.
"""

from pathlib import Path

from sqlalchemy import inspect

from app.core.config import settings


class TestTheEngineIsRedirectedToo:
    def test_the_engine_does_not_point_at_the_configured_database(self) -> None:
        """The invariant the session factories already had."""
        import app.core.db as db_module

        assert str(db_module.engine.url) != settings.DATABASE_URL

    def test_the_engine_writes_somewhere_disposable(self) -> None:
        """A temp file, not the repo's ``data/`` directory."""
        import app.core.db as db_module

        database = db_module.engine.url.database
        assert database is not None
        assert Path(database).resolve() != Path("data/app.db").resolve()

    def test_creating_tables_lands_them_in_the_test_database(self) -> None:
        """The behaviour, not just the wiring: the call that leaked is
        ``init_database``, so run it and see where the tables go."""
        import app.core.db as db_module
        from app.core.db import init_database

        init_database()

        names = set(inspect(db_module.engine).get_table_names())
        assert "finance_transaction" in names


class TestInitDatabaseActsOnTheEngineItWritesThrough:
    """``init_database`` created tables through ``engine`` but made the
    directory for, and reported, ``DATABASE_PATH`` - a module constant.
    Under the redirect above those are two different files, so the line
    that should have exposed this bug named the live ledger while writing
    to a temp one. That is why a `make check` run still looked like it was
    touching ``data/app.db`` long after it had stopped."""

    def test_it_returns_the_database_it_actually_initialized(self) -> None:
        import app.core.db as db_module
        from app.core.db import init_database

        initialized = init_database()

        assert initialized == Path(str(db_module.engine.url.database))
        assert initialized.resolve() != Path("data/app.db").resolve()
