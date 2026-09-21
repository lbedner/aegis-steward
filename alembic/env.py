"""
Alembic environment configuration for SQLModel integration.

This module configures Alembic to work with SQLModel and ensures
proper metadata detection for autogenerate functionality.
"""

# ruff: noqa: I001
import sys
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Import SQLModel and models to register metadata
from sqlmodel import SQLModel  # noqa: E402
from app.core.config import settings  # noqa: E402


from app.models.conversation import Conversation, ConversationMessage  # noqa: E402,F401


from app.services.finance.models import (  # noqa: E402,F401
    FinanceAccount,
    FinanceAttachment,
    FinanceBalanceSnapshot,
    FinanceBudget,
    FinanceBudgetCategory,
    FinanceCategory,
    FinanceCategoryAlias,
    FinanceConnection,
    FinanceCurrency,
    FinanceFxRate,
    FinanceHolding,
    FinanceImportBatch,
    FinanceImportBatchRow,
    FinanceImportProfile,
    FinanceInsight,
    FinanceInstitution,
    FinanceLiabilityDetail,
    FinanceMerchant,
    FinanceNetWorthSnapshot,
    FinanceRecurringStream,
    FinanceRule,
    FinanceSecurity,
    FinanceSecurityPrice,
    FinanceSpendingBaseline,
    FinanceTag,
    FinanceTrade,
    FinanceTransaction,
    FinanceTransactionChangelog,
    FinanceTransactionSplit,
    FinanceTransactionTag,
    FinanceTransfer,
    FinanceValuation,
    FinanceWebhookEvent,
)


from app.services.insurance.models import InsuranceClaim, InsurancePolicy  # noqa: E402,F401
from app.services.mail.models import (  # noqa: E402,F401
    MailAttachment,
    MailBatch,
    MailMessage,
)
from app.services.scheduler.models import JobExecution  # noqa: E402,F401


# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Set the SQLAlchemy URL from our settings - unless a caller already
# supplied a real one. ``alembic.command.upgrade`` is invoked in-process
# at startup and by tests against a throwaway file, and overwriting the
# url they set would silently point those runs at the configured
# database instead. The shipped ini still carries the placeholder, so
# the CLI path is unchanged.
PLACEHOLDER_URL = "driver://user:pass@localhost/dbname"

if (config.get_main_option("sqlalchemy.url") or PLACEHOLDER_URL) == PLACEHOLDER_URL:
    config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)


# Set target metadata to SQLModel.metadata
target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well. By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # include_schemas so autogenerate sees tables in non-public schemas
        # (e.g. the scheduler component's ``scheduler`` schema).
        include_schemas=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Every option goes through one dict, and
        # ``config.attributes["configure"]`` is the contract callers use to
        # override them - ``app.cli.migrate_gen`` sets ``include_object``,
        # ``compare_type`` and ``render_as_batch`` that way, and a caller's
        # value wins.
        #
        # Without this merge the generator's options were silently dropped,
        # and ``render_as_batch`` with them: autogenerate then emitted
        # ``ALTER TABLE ... ALTER COLUMN ... DROP NOT NULL``, which SQLite
        # has no syntax for, so every generated revision was unusable
        # (found adding auth, 2026-09-20). Assign defaults into ``options``
        # below, never as a second keyword on ``context.configure`` - that
        # raises ``TypeError: got multiple values`` the moment a caller
        # sets the same option.
        options: dict[str, object] = {
            "target_metadata": target_metadata,
            # include_schemas so autogenerate sees tables in non-public
            # schemas (e.g. the scheduler component's ``scheduler`` schema).
            "include_schemas": True,
        }
        options.update(config.attributes.get("configure", {}))
        context.configure(connection=connection, **options)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
