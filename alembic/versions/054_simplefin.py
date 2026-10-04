"""SimpleFIN can be a bank connection

Revision ID: 054
Revises: 053
Create Date: 2026-10-03 12:00:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "054"
down_revision = "053"
branch_labels = None
depends_on = None
aegis_stamp_signature = (
    "check",
    "finance_connection",
    "ck_finance_connection_provider",
    "simplefin",
)

# Spelled out rather than imported from the models: a migration records
# what the schema WAS and became at this revision, and a later edit to
# the models must not silently rewrite history.
_PROVIDERS = (
    "plaid",
    "snaptrade",
    "simplefin",
    "coinbase",
    "exchange_key",
    "onchain",
    "manual",
)
_SOURCES = (
    "plaid_sync",
    "snaptrade_sync",
    "simplefin_sync",
    "ofx",
    "qfx",
    "qif",
    "csv",
    "manual",
)
# (table, constraint, column, values)
_CHECKS = (
    ("finance_institution", "ck_finance_institution_provider", "provider", _PROVIDERS),
    ("finance_connection", "ck_finance_connection_provider", "provider", _PROVIDERS),
    ("finance_account", "ck_finance_account_provider", "provider", _PROVIDERS),
    ("finance_import_batch", "ck_finance_importbatch_source", "source_type", _SOURCES),
)


def _check(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def _rebuild(drop: frozenset[str] = frozenset()) -> None:
    """A CHECK constraint cannot be altered in place on SQLite, so each
    table is rebuilt with the new one (less ``drop``); batch mode copies."""
    for table, name, column, values in _CHECKS:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(name, type_="check")
            batch.create_check_constraint(
                name, _check(column, tuple(v for v in values if v not in drop))
            )


def upgrade() -> None:
    """SimpleFIN Bridge, what open-source budgeting apps use for US
    banks, as a third aggregator beside Plaid and SnapTrade (#370)."""
    _rebuild()


def downgrade() -> None:
    op.execute("DELETE FROM finance_import_batch WHERE source_type = 'simplefin_sync'")
    for table in ("finance_account", "finance_connection", "finance_institution"):
        op.execute(
            f"UPDATE {table} SET provider = 'manual' WHERE provider = 'simplefin'"
        )
    _rebuild(drop=frozenset({"simplefin", "simplefin_sync"}))
