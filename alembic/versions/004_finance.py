"""Finance service tables (currencies, fx rates)

Revision ID: 004
Revises: 003
Create Date: 2026-09-07 19:50:32.610353

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create and alter finance service tables."""

    # Create finance_currency table
    op.create_table(
        "finance_currency",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(8), nullable=True),
        sa.Column("decimals", sa.Integer(), nullable=False, default=2),
        sa.Column("kind", sa.String(8), nullable=False, default="fiat"),
        sa.Column("is_active", sa.Boolean(), nullable=False, default=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "kind IN ('fiat', 'crypto')", name="ck_finance_currency_kind"
        ),
        sa.CheckConstraint(
            "decimals BETWEEN 0 AND 18", name="ck_finance_currency_decimals"
        ),
    )

    op.create_index(
        op.f("ix_finance_currency_code"), "finance_currency", ["code"], unique=True
    )

    op.create_index(op.f("ix_finance_currency_kind"), "finance_currency", ["kind"])

    # Create finance_fx_rate table
    op.create_table(
        "finance_fx_rate",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("base_currency", sa.String(16), nullable=False),
        sa.Column("quote_currency", sa.String(16), nullable=False),
        sa.Column("rate_date", sa.Date(), nullable=False),
        sa.Column("rate_e8", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False, default="manual"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["base_currency"], ["finance_currency.code"]),
        sa.ForeignKeyConstraint(["quote_currency"], ["finance_currency.code"]),
        sa.CheckConstraint(
            "source IN ('manual', 'ecb', 'exchange_api', 'coingecko', 'provider', 'derived')",
            name="ck_finance_fxrate_source",
        ),
        sa.CheckConstraint(
            "base_currency <> quote_currency", name="ck_finance_fxrate_distinct"
        ),
    )

    op.create_index(
        op.f("ix_finance_fxrate_pair_date"),
        "finance_fx_rate",
        ["base_currency", "quote_currency", "rate_date"],
    )

    op.create_index(
        op.f("uq_finance_fxrate"),
        "finance_fx_rate",
        ["base_currency", "quote_currency", "rate_date", "source"],
        unique=True,
    )

    # Create finance_icon table
    op.create_table(
        "finance_icon",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("icon_b64", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_finance_icon_domain"), "finance_icon", ["domain"], unique=True
    )

    # Create finance_institution table
    op.create_table(
        "finance_institution",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("provider_institution_id", sa.Text(), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("domain", sa.String(255), nullable=True),
        sa.Column("logo_url", sa.Text(), nullable=True),
        sa.Column("primary_color", sa.String(16), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("oauth_required", sa.Boolean(), nullable=False, default=False),
        sa.Column(
            "uses_tokenized_account_numbers",
            sa.Boolean(),
            nullable=False,
            default=False,
        ),
        sa.Column("uses_app_to_app", sa.Boolean(), nullable=False, default=False),
        sa.Column("supported_products", sa.JSON(), nullable=False, default=[]),
        sa.Column("metadata", sa.JSON(), nullable=False, default={}),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "provider IN ('plaid', 'snaptrade', 'coinbase', 'exchange_key', 'onchain', 'manual')",
            name="ck_finance_institution_provider",
        ),
    )

    op.create_index(
        op.f("ix_finance_institution_provider"), "finance_institution", ["provider"]
    )

    op.create_index(
        op.f("ix_finance_institution_name"), "finance_institution", ["name"]
    )

    op.create_index(
        op.f("uq_finance_institution_provider_extid"),
        "finance_institution",
        ["provider", "provider_institution_id"],
        unique=True,
        sqlite_where=sa.text("provider_institution_id IS NOT NULL"),
        postgresql_where=sa.text("provider_institution_id IS NOT NULL"),
    )

    # Create finance_connection table
    op.create_table(
        "finance_connection",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("institution_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("connection_type", sa.String(20), nullable=False),
        sa.Column("provider_item_id", sa.Text(), nullable=True),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column("environment", sa.String(16), nullable=False, default="sandbox"),
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("api_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("api_passphrase_encrypted", sa.Text(), nullable=True),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("wallet_address", sa.Text(), nullable=True),
        sa.Column("wallet_chain", sa.Text(), nullable=True),
        sa.Column("capabilities", sa.JSON(), nullable=False, default={}),
        sa.Column("status", sa.String(24), nullable=False, default="healthy"),
        sa.Column("status_detail", sa.Text(), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column("needs_user_action", sa.Boolean(), nullable=False, default=False),
        sa.Column("sync_cursor", sa.Text(), nullable=True),
        sa.Column("days_requested", sa.Integer(), nullable=True),
        sa.Column("consent_expiration_at", sa.DateTime(), nullable=True),
        sa.Column("last_successful_sync_at", sa.DateTime(), nullable=True),
        sa.Column("last_sync_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("removed_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, default={}),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["institution_id"], ["finance_institution.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "provider IN ('plaid', 'snaptrade', 'coinbase', 'exchange_key', 'onchain', 'manual')",
            name="ck_finance_connection_provider",
        ),
        sa.CheckConstraint(
            "connection_type IN ('oauth_access_token', 'api_key_secret', 'onchain_address', 'aggregator_token', 'manual')",
            name="ck_finance_connection_type",
        ),
        sa.CheckConstraint(
            "environment IN ('sandbox', 'production')",
            name="ck_finance_connection_environment",
        ),
        sa.CheckConstraint(
            "status IN ('healthy', 'login_required', 'pending_expiration', 'pending_disconnect', 'consent_expired', 'revoked', 'error', 'loading', 'manual')",
            name="ck_finance_connection_status",
        ),
    )

    op.create_index(
        op.f("ix_finance_connection_owner"), "finance_connection", ["owner_user_id"]
    )

    op.create_index(
        op.f("ix_finance_connection_org"), "finance_connection", ["organization_id"]
    )

    op.create_index(
        op.f("ix_finance_connection_institution"),
        "finance_connection",
        ["institution_id"],
    )

    op.create_index(
        op.f("ix_finance_connection_needs_action"),
        "finance_connection",
        ["needs_user_action"],
    )

    op.create_index(
        op.f("ix_finance_connection_deleted"), "finance_connection", ["deleted_at"]
    )

    op.create_index(
        op.f("ix_finance_connection_owner_status"),
        "finance_connection",
        ["owner_user_id", "status"],
    )

    op.create_index(
        op.f("uq_finance_connection_provider_item"),
        "finance_connection",
        ["provider", "provider_item_id"],
        unique=True,
        sqlite_where=sa.text("provider_item_id IS NOT NULL AND deleted_at IS NULL"),
        postgresql_where=sa.text("provider_item_id IS NOT NULL AND deleted_at IS NULL"),
    )

    op.create_index(
        op.f("uq_finance_connection_wallet"),
        "finance_connection",
        ["owner_user_id", "provider", "wallet_address"],
        unique=True,
        sqlite_where=sa.text("wallet_address IS NOT NULL"),
        postgresql_where=sa.text("wallet_address IS NOT NULL"),
    )

    # Create finance_webhook_event table
    op.create_table(
        "finance_webhook_event",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("provider_item_id", sa.Text(), nullable=True),
        sa.Column("webhook_type", sa.Text(), nullable=True),
        sa.Column("webhook_code", sa.Text(), nullable=True),
        sa.Column("provider_event_id", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, default={}),
        sa.Column("status", sa.String(16), nullable=False, default="received"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["finance_connection.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "provider IN ('plaid', 'snaptrade', 'coinbase')",
            name="ck_finance_webhook_provider",
        ),
        sa.CheckConstraint(
            "status IN ('received', 'processed', 'ignored', 'error')",
            name="ck_finance_webhook_status",
        ),
    )

    op.create_index(
        op.f("ix_finance_webhook_connection"),
        "finance_webhook_event",
        ["connection_id"],
    )

    op.create_index(
        op.f("ix_finance_webhook_item"), "finance_webhook_event", ["provider_item_id"]
    )

    op.create_index(
        op.f("ix_finance_webhook_status_received"),
        "finance_webhook_event",
        ["status", "received_at"],
    )

    op.create_index(
        op.f("uq_finance_webhook_event"),
        "finance_webhook_event",
        ["provider", "provider_event_id"],
        unique=True,
        sqlite_where=sa.text("provider_event_id IS NOT NULL"),
        postgresql_where=sa.text("provider_event_id IS NOT NULL"),
    )

    # Create finance_subject table
    op.create_table(
        "finance_subject",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, default="person"),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "kind IN ('person', 'trust', 'estate', 'entity')",
            name="ck_finance_subject_kind",
        ),
    )

    op.create_index(
        op.f("ix_finance_subject_owner"), "finance_subject", ["owner_user_id"]
    )

    # Create finance_account table
    op.create_table(
        "finance_account",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("subject_id", sa.Integer(), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column("institution_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("provider_account_id", sa.Text(), nullable=True),
        sa.Column("persistent_account_id", sa.Text(), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("official_name", sa.String(255), nullable=True),
        sa.Column("mask", sa.String(8), nullable=True),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("subtype", sa.Text(), nullable=True),
        sa.Column("account_type", sa.String(24), nullable=False),
        sa.Column("classification", sa.String(12), nullable=False),
        sa.Column("currency", sa.String(16), nullable=False, default="usd"),
        sa.Column("current_balance", sa.BigInteger(), nullable=True),
        sa.Column("available_balance", sa.BigInteger(), nullable=True),
        sa.Column("credit_limit", sa.BigInteger(), nullable=True),
        sa.Column("balance_as_of", sa.DateTime(), nullable=True),
        sa.Column("is_manual", sa.Boolean(), nullable=False, default=False),
        sa.Column("is_hidden", sa.Boolean(), nullable=False, default=False),
        sa.Column("is_closed", sa.Boolean(), nullable=False, default=False),
        sa.Column("is_on_budget", sa.Boolean(), nullable=False, default=True),
        sa.Column("linked_at", sa.DateTime(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, default={}),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["finance_connection.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["institution_id"], ["finance_institution.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["currency"], ["finance_currency.code"]),
        sa.ForeignKeyConstraint(["subject_id"], ["finance_subject.id"]),
        sa.CheckConstraint(
            "account_type IN ('checking', 'savings', 'credit_card', 'loan', 'investment', 'brokerage', 'crypto', 'property', 'vehicle', 'cash', 'goal', 'envelope', 'other_asset', 'other_liability')",
            name="ck_finance_account_type",
        ),
        sa.CheckConstraint(
            "classification IN ('asset', 'liability')",
            name="ck_finance_account_classification",
        ),
        sa.CheckConstraint(
            "provider IN ('plaid', 'snaptrade', 'coinbase', 'exchange_key', 'onchain', 'manual')",
            name="ck_finance_account_provider",
        ),
    )

    op.create_index(
        op.f("ix_finance_account_owner"), "finance_account", ["owner_user_id"]
    )

    op.create_index(
        op.f("ix_finance_account_subject_id"), "finance_account", ["subject_id"]
    )

    op.create_index(
        op.f("ix_finance_account_org"), "finance_account", ["organization_id"]
    )

    op.create_index(
        op.f("ix_finance_account_connection"), "finance_account", ["connection_id"]
    )

    op.create_index(
        op.f("ix_finance_account_institution"), "finance_account", ["institution_id"]
    )

    op.create_index(
        op.f("ix_finance_account_persistent"),
        "finance_account",
        ["persistent_account_id"],
    )

    op.create_index(
        op.f("ix_finance_account_deleted"), "finance_account", ["deleted_at"]
    )

    op.create_index(
        op.f("ix_finance_account_owner_type"),
        "finance_account",
        ["owner_user_id", "account_type"],
    )

    op.create_index(
        op.f("ix_finance_account_owner_classification"),
        "finance_account",
        ["owner_user_id", "classification"],
    )

    op.create_index(
        op.f("uq_finance_account_provider"),
        "finance_account",
        ["connection_id", "provider_account_id"],
        unique=True,
        sqlite_where=sa.text("provider_account_id IS NOT NULL AND deleted_at IS NULL"),
        postgresql_where=sa.text(
            "provider_account_id IS NOT NULL AND deleted_at IS NULL"
        ),
    )

    # Create finance_liability_detail table
    op.create_table(
        "finance_liability_detail",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("liability_type", sa.Text(), nullable=True),
        sa.Column("last_statement_balance", sa.BigInteger(), nullable=True),
        sa.Column("last_statement_issue_date", sa.Date(), nullable=True),
        sa.Column("last_payment_amount", sa.BigInteger(), nullable=True),
        sa.Column("last_payment_date", sa.Date(), nullable=True),
        sa.Column("minimum_payment_amount", sa.BigInteger(), nullable=True),
        sa.Column("next_payment_due_date", sa.Date(), nullable=True),
        sa.Column("origination_date", sa.Date(), nullable=True),
        sa.Column("origination_principal", sa.BigInteger(), nullable=True),
        sa.Column("outstanding_balance", sa.BigInteger(), nullable=True),
        sa.Column("interest_rate_bps", sa.Integer(), nullable=True),
        sa.Column("ytd_interest_paid", sa.BigInteger(), nullable=True),
        sa.Column("ytd_principal_paid", sa.BigInteger(), nullable=True),
        sa.Column("loan_term_months", sa.Integer(), nullable=True),
        sa.Column("is_overdue", sa.Boolean(), nullable=True),
        sa.Column("secured_by_account_id", sa.Integer(), nullable=True),
        sa.Column("lien_position", sa.Integer(), nullable=True),
        sa.Column("aprs", sa.JSON(), nullable=False, default=[]),
        sa.Column("currency", sa.String(16), nullable=False, default="usd"),
        sa.Column("raw", sa.JSON(), nullable=False, default={}),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["finance_account.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["secured_by_account_id"], ["finance_account.id"]),
        sa.ForeignKeyConstraint(["currency"], ["finance_currency.code"]),
    )

    op.create_index(
        op.f("ix_finance_liability_owner"),
        "finance_liability_detail",
        ["owner_user_id"],
    )

    op.create_index(
        op.f("ix_finance_liability_account"), "finance_liability_detail", ["account_id"]
    )

    op.create_index(
        op.f("uq_finance_liability_account"),
        "finance_liability_detail",
        ["account_id"],
        unique=True,
    )

    # Create finance_pending_change table
    op.create_table(
        "finance_pending_change",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("change_type", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("proposed_by_agent", sa.String(64), nullable=True),
        sa.Column("conversation_id", sa.String(64), nullable=True),
        sa.Column("batch_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, default="pending"),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired')",
            name="ck_finance_pending_status",
        ),
    )

    op.create_index(
        op.f("ix_finance_pending_owner"), "finance_pending_change", ["owner_user_id"]
    )

    op.create_index(
        op.f("ix_finance_pending_status"), "finance_pending_change", ["status"]
    )

    op.create_index(
        op.f("ix_finance_pending_change_batch_id"),
        "finance_pending_change",
        ["batch_id"],
    )

    # Create finance_balance_snapshot table
    op.create_table(
        "finance_balance_snapshot",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("balance_date", sa.Date(), nullable=False),
        sa.Column("balance", sa.BigInteger(), nullable=False, default=0),
        sa.Column("available_balance", sa.BigInteger(), nullable=True),
        sa.Column("cash_balance", sa.BigInteger(), nullable=True),
        sa.Column("holdings_value", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(16), nullable=False, default="usd"),
        sa.Column("base_currency_value", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, default="sync"),
        sa.Column("is_estimated", sa.Boolean(), nullable=False, default=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["finance_account.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["currency"], ["finance_currency.code"]),
        sa.CheckConstraint(
            "source IN ('sync', 'provider', 'computed', 'carried_forward', 'manual')",
            name="ck_finance_balsnap_source",
        ),
    )

    op.create_index(
        op.f("ix_finance_balsnap_account_date"),
        "finance_balance_snapshot",
        ["account_id", "balance_date"],
    )

    op.create_index(
        op.f("ix_finance_balsnap_owner_date"),
        "finance_balance_snapshot",
        ["owner_user_id", "balance_date"],
    )

    op.create_index(
        op.f("uq_finance_balsnap"),
        "finance_balance_snapshot",
        ["account_id", "balance_date"],
        unique=True,
    )

    # Create finance_net_worth_snapshot table
    op.create_table(
        "finance_net_worth_snapshot",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("total_assets_amount", sa.BigInteger(), nullable=False, default=0),
        sa.Column(
            "total_liabilities_amount", sa.BigInteger(), nullable=False, default=0
        ),
        sa.Column("net_worth_amount", sa.BigInteger(), nullable=False, default=0),
        sa.Column("cash_amount", sa.BigInteger(), nullable=True),
        sa.Column("investments_amount", sa.BigInteger(), nullable=True),
        sa.Column("other_assets_amount", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(16), nullable=False, default="usd"),
        sa.Column("breakdown", sa.JSON(), nullable=False, default={}),
        sa.Column("is_estimated", sa.Boolean(), nullable=False, default=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["currency"], ["finance_currency.code"]),
    )

    op.create_index(
        op.f("ix_finance_networth_owner_date"),
        "finance_net_worth_snapshot",
        ["owner_user_id", "as_of_date"],
    )

    op.create_index(
        op.f("ix_finance_networth_org_date"),
        "finance_net_worth_snapshot",
        ["organization_id", "as_of_date"],
    )

    op.create_index(
        op.f("uq_finance_networth"),
        "finance_net_worth_snapshot",
        ["owner_user_id", "as_of_date", "currency"],
        unique=True,
    )

    # Create finance_valuation table
    op.create_table(
        "finance_valuation",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("value", sa.BigInteger(), nullable=False, default=0),
        sa.Column("currency", sa.String(16), nullable=False, default="usd"),
        sa.Column("source", sa.String(16), nullable=False, default="manual"),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("is_estimate", sa.Boolean(), nullable=False, default=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=True),
        sa.Column("is_stale", sa.Boolean(), nullable=False, default=False),
        sa.Column("stale_after_days", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, default={}),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["finance_account.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["currency"], ["finance_currency.code"]),
        sa.CheckConstraint(
            "source IN ('manual', 'zillow', 'kbb', 'exchange_api', 'onchain', 'plaid', 'snaptrade', 'coingecko', 'reconciliation', 'goal_auto', 'envelope_auto')",
            name="ck_finance_valuation_source",
        ),
    )

    op.create_index(
        op.f("ix_finance_valuation_owner_date"),
        "finance_valuation",
        ["owner_user_id", "as_of_date"],
    )

    op.create_index(
        op.f("ix_finance_valuation_account_date"),
        "finance_valuation",
        ["account_id", "as_of_date"],
    )

    op.create_index(
        op.f("ix_finance_valuation_source"), "finance_valuation", ["source"]
    )

    op.create_index(
        op.f("uq_finance_valuation"),
        "finance_valuation",
        ["account_id", "as_of_date", "source"],
        unique=True,
    )

    # Create finance_transaction table
    op.create_table(
        "finance_transaction",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column("import_batch_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("external_id_source", sa.Text(), nullable=True),
        sa.Column("import_hash", sa.String(64), nullable=True),
        sa.Column("within_day_ordinal", sa.Integer(), nullable=False, default=0),
        sa.Column("dedup_status", sa.String(16), nullable=False, default="unique"),
        sa.Column("canonical_transaction_id", sa.Integer(), nullable=True),
        sa.Column("source_precedence", sa.Integer(), nullable=False, default=0),
        sa.Column("amount", sa.BigInteger(), nullable=False, default=0),
        sa.Column("raw_amount", sa.BigInteger(), nullable=True),
        sa.Column("raw_sign_convention", sa.Text(), nullable=True),
        sa.Column("currency", sa.String(16), nullable=False, default="usd"),
        sa.Column("unofficial_currency_code", sa.Text(), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("authorized_date", sa.Date(), nullable=True),
        sa.Column("datetime", sa.DateTime(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("original_description", sa.Text(), nullable=True),
        sa.Column("merchant_id", sa.Integer(), nullable=True),
        sa.Column("merchant_name", sa.Text(), nullable=True),
        sa.Column("merchant_entity_id", sa.Text(), nullable=True),
        sa.Column("memo", sa.Text(), nullable=True),
        sa.Column("check_number", sa.String(32), nullable=True),
        sa.Column("payment_channel", sa.Text(), nullable=True),
        sa.Column("pfc_primary", sa.Text(), nullable=True),
        sa.Column("pfc_detailed", sa.Text(), nullable=True),
        sa.Column("pfc_confidence_level", sa.Text(), nullable=True),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("category_source", sa.String(12), nullable=False, default="unset"),
        sa.Column("is_user_categorized", sa.Boolean(), nullable=False, default=False),
        sa.Column("is_reviewed", sa.Boolean(), nullable=False, default=False),
        sa.Column("pending", sa.Boolean(), nullable=False, default=False),
        sa.Column("pending_provider_id", sa.Text(), nullable=True),
        sa.Column("pending_transaction_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, default="posted"),
        sa.Column("is_transfer", sa.Boolean(), nullable=False, default=False),
        sa.Column("transfer_group_id", sa.Integer(), nullable=True),
        sa.Column("transfer_pair_transaction_id", sa.Integer(), nullable=True),
        sa.Column("is_split", sa.Boolean(), nullable=False, default=False),
        sa.Column("excluded_from_reports", sa.Boolean(), nullable=False, default=False),
        sa.Column("is_reversal", sa.Boolean(), nullable=False, default=False),
        sa.Column("reverses_transaction_id", sa.Integer(), nullable=True),
        sa.Column("recurring_stream_id", sa.Integer(), nullable=True),
        sa.Column(
            "reconciled_status", sa.String(12), nullable=False, default="uncleared"
        ),
        sa.Column("location", sa.JSON(), nullable=True),
        sa.Column("counterparties", sa.JSON(), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("is_removed", sa.Boolean(), nullable=False, default=False),
        sa.Column("removed_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, default={}),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["finance_account.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["finance_connection.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["currency"], ["finance_currency.code"]),
        sa.ForeignKeyConstraint(
            ["canonical_transaction_id"],
            ["finance_transaction.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["pending_transaction_id"], ["finance_transaction.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["transfer_pair_transaction_id"],
            ["finance_transaction.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reverses_transaction_id"], ["finance_transaction.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "source IN ('plaid', 'snaptrade', 'ofx', 'qfx', 'qif', 'csv', 'manual', 'coinbase', 'onchain', 'simplefin', 'teller')",
            name="ck_finance_txn_source",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'posted', 'removed')", name="ck_finance_txn_status"
        ),
        sa.CheckConstraint(
            "dedup_status IN ('unique', 'primary', 'duplicate', 'linked')",
            name="ck_finance_txn_dedup_status",
        ),
        sa.CheckConstraint(
            "category_source IN ('provider', 'ml', 'rule', 'user', 'unset')",
            name="ck_finance_txn_category_source",
        ),
        sa.CheckConstraint(
            "reconciled_status IN ('uncleared', 'cleared', 'reconciled')",
            name="ck_finance_txn_reconciled",
        ),
        sa.CheckConstraint(
            "NOT (external_id IS NOT NULL AND import_hash IS NOT NULL)",
            name="ck_finance_txn_dedup_lane",
        ),
    )

    op.create_index(
        op.f("ix_finance_txn_owner_date"),
        "finance_transaction",
        ["owner_user_id", "date"],
    )

    op.create_index(
        op.f("ix_finance_txn_account_date"),
        "finance_transaction",
        ["account_id", "date"],
    )

    op.create_index(
        op.f("ix_finance_txn_owner_cat_date"),
        "finance_transaction",
        ["owner_user_id", "category_id", "date"],
    )

    op.create_index(
        op.f("ix_finance_txn_merchant"), "finance_transaction", ["merchant_id"]
    )

    op.create_index(
        op.f("ix_finance_txn_merchant_entity"),
        "finance_transaction",
        ["merchant_entity_id"],
    )

    op.create_index(
        op.f("ix_finance_txn_category"), "finance_transaction", ["category_id"]
    )

    op.create_index(
        op.f("ix_finance_txn_connection"), "finance_transaction", ["connection_id"]
    )

    op.create_index(
        op.f("ix_finance_txn_batch"), "finance_transaction", ["import_batch_id"]
    )

    op.create_index(
        op.f("ix_finance_txn_recurring"), "finance_transaction", ["recurring_stream_id"]
    )

    op.create_index(
        op.f("ix_finance_txn_transfer_group"),
        "finance_transaction",
        ["transfer_group_id"],
    )

    op.create_index(
        op.f("ix_finance_txn_canonical"),
        "finance_transaction",
        ["canonical_transaction_id"],
    )

    op.create_index(
        op.f("ix_finance_txn_pending_link"),
        "finance_transaction",
        ["pending_transaction_id"],
    )

    op.create_index(
        op.f("ix_finance_txn_pair"),
        "finance_transaction",
        ["transfer_pair_transaction_id"],
    )

    op.create_index(
        op.f("ix_finance_txn_reverses"),
        "finance_transaction",
        ["reverses_transaction_id"],
    )

    op.create_index(op.f("ix_finance_txn_pending"), "finance_transaction", ["pending"])

    op.create_index(
        op.f("ix_finance_txn_is_transfer"), "finance_transaction", ["is_transfer"]
    )

    op.create_index(
        op.f("ix_finance_txn_deleted"), "finance_transaction", ["deleted_at"]
    )

    op.create_index(
        op.f("uq_finance_txn_external"),
        "finance_transaction",
        ["account_id", "source", "external_id"],
        unique=True,
        sqlite_where=sa.text("external_id IS NOT NULL AND deleted_at IS NULL"),
        postgresql_where=sa.text("external_id IS NOT NULL AND deleted_at IS NULL"),
    )

    op.create_index(
        op.f("uq_finance_txn_hash"),
        "finance_transaction",
        ["account_id", "import_hash"],
        unique=True,
        sqlite_where=sa.text(
            "external_id IS NULL AND import_hash IS NOT NULL AND deleted_at IS NULL"
        ),
        postgresql_where=sa.text(
            "external_id IS NULL AND import_hash IS NOT NULL AND deleted_at IS NULL"
        ),
    )

    # Create finance_transaction_split table
    op.create_table(
        "finance_transaction_split",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("parent_transaction_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("merchant_id", sa.Integer(), nullable=True),
        sa.Column("amount", sa.BigInteger(), nullable=False, default=0),
        sa.Column("currency", sa.String(16), nullable=False, default="usd"),
        sa.Column("memo", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, default=0),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["parent_transaction_id"], ["finance_transaction.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["currency"], ["finance_currency.code"]),
    )

    op.create_index(
        op.f("ix_finance_split_parent"),
        "finance_transaction_split",
        ["parent_transaction_id"],
    )

    op.create_index(
        op.f("ix_finance_split_owner"), "finance_transaction_split", ["owner_user_id"]
    )

    op.create_index(
        op.f("ix_finance_split_category"), "finance_transaction_split", ["category_id"]
    )

    op.create_index(
        op.f("ix_finance_split_merchant"), "finance_transaction_split", ["merchant_id"]
    )

    op.create_index(
        op.f("uq_finance_split_parent_sort"),
        "finance_transaction_split",
        ["parent_transaction_id", "sort_order"],
        unique=True,
    )

    # Create finance_transfer table
    op.create_table(
        "finance_transfer",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("from_account_id", sa.Integer(), nullable=True),
        sa.Column("to_account_id", sa.Integer(), nullable=True),
        sa.Column("from_transaction_id", sa.Integer(), nullable=True),
        sa.Column("to_transaction_id", sa.Integer(), nullable=True),
        sa.Column("amount", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(16), nullable=False, default="usd"),
        sa.Column("transfer_date", sa.Date(), nullable=True),
        sa.Column("transfer_group_key", sa.Text(), nullable=True),
        sa.Column(
            "is_credit_card_payment", sa.Boolean(), nullable=False, default=False
        ),
        sa.Column("match_method", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, default="suggested"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["from_account_id"], ["finance_account.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["to_account_id"], ["finance_account.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["from_transaction_id"], ["finance_transaction.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["to_transaction_id"], ["finance_transaction.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["currency"], ["finance_currency.code"]),
        sa.CheckConstraint(
            "match_method IN ('auto_amount_date', 'plaid_transfer', 'user_manual', 'rule', 'payment_history')",
            name="ck_finance_transfer_method",
        ),
        sa.CheckConstraint(
            "status IN ('suggested', 'confirmed', 'rejected')",
            name="ck_finance_transfer_status",
        ),
        sa.CheckConstraint(
            "from_transaction_id IS NULL OR to_transaction_id IS NULL OR from_transaction_id <> to_transaction_id",
            name="ck_finance_transfer_distinct",
        ),
    )

    op.create_index(
        op.f("ix_finance_transfer_owner"), "finance_transfer", ["owner_user_id"]
    )

    op.create_index(
        op.f("ix_finance_transfer_from_account"),
        "finance_transfer",
        ["from_account_id"],
    )

    op.create_index(
        op.f("ix_finance_transfer_to_account"), "finance_transfer", ["to_account_id"]
    )

    op.create_index(
        op.f("ix_finance_transfer_from_txn"),
        "finance_transfer",
        ["from_transaction_id"],
    )

    op.create_index(
        op.f("ix_finance_transfer_to_txn"), "finance_transfer", ["to_transaction_id"]
    )

    op.create_index(
        op.f("ix_finance_transfer_group_key"),
        "finance_transfer",
        ["transfer_group_key"],
    )

    op.create_index(
        op.f("uq_finance_transfer_from"),
        "finance_transfer",
        ["from_transaction_id"],
        unique=True,
        sqlite_where=sa.text("from_transaction_id IS NOT NULL"),
        postgresql_where=sa.text("from_transaction_id IS NOT NULL"),
    )

    op.create_index(
        op.f("uq_finance_transfer_to"),
        "finance_transfer",
        ["to_transaction_id"],
        unique=True,
        sqlite_where=sa.text("to_transaction_id IS NOT NULL"),
        postgresql_where=sa.text("to_transaction_id IS NOT NULL"),
    )

    # Create finance_category table
    op.create_table(
        "finance_category",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("slug", sa.String(96), nullable=False),
        sa.Column("classification", sa.String(12), nullable=False),
        sa.Column("plaid_pfc_primary", sa.Text(), nullable=True),
        sa.Column("plaid_pfc_detailed", sa.Text(), nullable=True),
        sa.Column("icon", sa.String(64), nullable=True),
        sa.Column("color", sa.String(16), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, default=False),
        sa.Column("is_archived", sa.Boolean(), nullable=False, default=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, default=0),
        sa.Column("tax_line", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, default={}),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["finance_category.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "classification IN ('income', 'expense', 'transfer')",
            name="ck_finance_category_classification",
        ),
    )

    op.create_index(
        op.f("ix_finance_category_owner"), "finance_category", ["owner_user_id"]
    )

    op.create_index(
        op.f("ix_finance_category_parent"), "finance_category", ["parent_id"]
    )

    op.create_index(
        op.f("ix_finance_category_pfc"), "finance_category", ["plaid_pfc_detailed"]
    )

    op.create_index(
        op.f("uq_finance_category_system_slug"),
        "finance_category",
        ["slug"],
        unique=True,
        sqlite_where=sa.text("owner_user_id IS NULL"),
        postgresql_where=sa.text("owner_user_id IS NULL"),
    )

    op.create_index(
        op.f("uq_finance_category_user_slug"),
        "finance_category",
        ["owner_user_id", "slug"],
        unique=True,
        sqlite_where=sa.text("owner_user_id IS NOT NULL"),
        postgresql_where=sa.text("owner_user_id IS NOT NULL"),
    )

    # Create finance_category_alias table
    op.create_table(
        "finance_category_alias",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("alias_text", sa.Text(), nullable=False),
        sa.Column("normalized_alias", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["category_id"], ["finance_category.id"], ondelete="CASCADE"
        ),
    )

    op.create_index(
        op.f("ix_finance_catalias_owner"), "finance_category_alias", ["owner_user_id"]
    )

    op.create_index(
        op.f("ix_finance_catalias_category"), "finance_category_alias", ["category_id"]
    )

    op.create_index(
        op.f("ix_finance_catalias_normalized"),
        "finance_category_alias",
        ["normalized_alias"],
    )

    op.create_index(
        op.f("uq_finance_catalias_owner_norm"),
        "finance_category_alias",
        ["owner_user_id", "normalized_alias"],
        unique=True,
    )

    # Create finance_merchant table
    op.create_table(
        "finance_merchant",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("provider_merchant_id", sa.Text(), nullable=True),
        sa.Column("logo_url", sa.Text(), nullable=True),
        sa.Column("website_url", sa.Text(), nullable=True),
        sa.Column("default_category_id", sa.Integer(), nullable=True),
        sa.Column("service_type", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["default_category_id"], ["finance_category.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "source IN ('plaid', 'user', 'system', 'rule', 'snaptrade')",
            name="ck_finance_merchant_source",
        ),
    )

    op.create_index(
        op.f("ix_finance_merchant_owner"), "finance_merchant", ["owner_user_id"]
    )

    op.create_index(
        op.f("ix_finance_merchant_org"), "finance_merchant", ["organization_id"]
    )

    op.create_index(
        op.f("ix_finance_merchant_normalized"), "finance_merchant", ["normalized_name"]
    )

    op.create_index(
        op.f("ix_finance_merchant_default_cat"),
        "finance_merchant",
        ["default_category_id"],
    )

    op.create_index(
        op.f("ix_finance_merchant_deleted"), "finance_merchant", ["deleted_at"]
    )

    op.create_index(
        op.f("uq_finance_merchant_global"),
        "finance_merchant",
        ["normalized_name"],
        unique=True,
        sqlite_where=sa.text("owner_user_id IS NULL AND deleted_at IS NULL"),
        postgresql_where=sa.text("owner_user_id IS NULL AND deleted_at IS NULL"),
    )

    op.create_index(
        op.f("uq_finance_merchant_user"),
        "finance_merchant",
        ["owner_user_id", "normalized_name"],
        unique=True,
        sqlite_where=sa.text("owner_user_id IS NOT NULL AND deleted_at IS NULL"),
        postgresql_where=sa.text("owner_user_id IS NOT NULL AND deleted_at IS NULL"),
    )

    op.create_index(
        op.f("uq_finance_merchant_provider"),
        "finance_merchant",
        ["source", "provider_merchant_id"],
        unique=True,
        sqlite_where=sa.text("provider_merchant_id IS NOT NULL"),
        postgresql_where=sa.text("provider_merchant_id IS NOT NULL"),
    )

    # Create finance_tag table
    op.create_table(
        "finance_tag",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("normalized_name", sa.String(64), nullable=False),
        sa.Column("color", sa.String(16), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(op.f("ix_finance_tag_owner"), "finance_tag", ["owner_user_id"])

    op.create_index(op.f("ix_finance_tag_org"), "finance_tag", ["organization_id"])

    op.create_index(op.f("ix_finance_tag_deleted"), "finance_tag", ["deleted_at"])

    op.create_index(
        op.f("uq_finance_tag_owner_name"),
        "finance_tag",
        ["owner_user_id", "normalized_name"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL"),
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # Create finance_transaction_tag table
    op.create_table(
        "finance_transaction_tag",
        sa.Column("transaction_id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("tag_id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("split_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("transaction_id", "tag_id"),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["finance_transaction.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tag_id"], ["finance_tag.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["split_id"], ["finance_transaction_split.id"], ondelete="CASCADE"
        ),
    )

    op.create_index(
        op.f("ix_finance_txntag_tag"), "finance_transaction_tag", ["tag_id"]
    )

    op.create_index(
        op.f("ix_finance_txntag_split"), "finance_transaction_tag", ["split_id"]
    )

    # Create finance_rule table
    op.create_table(
        "finance_rule",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, default=100),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, default=True),
        sa.Column("conditions", sa.JSON(), nullable=False, default={}),
        sa.Column("actions", sa.JSON(), nullable=False, default={}),
        sa.Column("stop_processing", sa.Boolean(), nullable=False, default=False),
        sa.Column("match_count", sa.Integer(), nullable=False, default=0),
        sa.Column("last_matched_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, default={}),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(op.f("ix_finance_rule_owner"), "finance_rule", ["owner_user_id"])

    op.create_index(op.f("ix_finance_rule_org"), "finance_rule", ["organization_id"])

    op.create_index(
        op.f("ix_finance_rule_owner_priority"),
        "finance_rule",
        ["owner_user_id", "priority"],
    )

    op.create_index(op.f("ix_finance_rule_deleted"), "finance_rule", ["deleted_at"])

    # Create finance_security table
    op.create_table(
        "finance_security",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("provider_security_id", sa.Text(), nullable=True),
        sa.Column("figi", sa.Text(), nullable=True),
        sa.Column("cusip", sa.String(16), nullable=True),
        sa.Column("isin", sa.String(16), nullable=True),
        sa.Column("sedol", sa.String(16), nullable=True),
        sa.Column("ticker", sa.String(32), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("security_type", sa.Text(), nullable=True),
        sa.Column("exchange_mic", sa.String(10), nullable=True),
        sa.Column("exchange_operating_mic", sa.String(10), nullable=True),
        sa.Column("country_code", sa.String(2), nullable=True),
        sa.Column("currency", sa.String(16), nullable=True),
        sa.Column("is_cash_equivalent", sa.Boolean(), nullable=False, default=False),
        sa.Column("is_crypto", sa.Boolean(), nullable=False, default=False),
        sa.Column("coingecko_id", sa.Text(), nullable=True),
        sa.Column("onchain_contract", sa.Text(), nullable=True),
        sa.Column("onchain_chain", sa.Text(), nullable=True),
        sa.Column("close_price", sa.BigInteger(), nullable=True),
        sa.Column("price_scale", sa.Integer(), nullable=False, default=2),
        sa.Column("close_price_as_of", sa.Date(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, default={}),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["currency"], ["finance_currency.code"]),
    )

    op.create_index(op.f("ix_finance_security_ticker"), "finance_security", ["ticker"])

    op.create_index(op.f("ix_finance_security_cusip"), "finance_security", ["cusip"])

    op.create_index(op.f("ix_finance_security_isin"), "finance_security", ["isin"])

    op.create_index(
        op.f("ix_finance_security_provider_secid"),
        "finance_security",
        ["provider_security_id"],
    )

    op.create_index(
        op.f("ix_finance_security_type"), "finance_security", ["security_type"]
    )

    op.create_index(
        op.f("uq_finance_security_provider"),
        "finance_security",
        ["provider", "provider_security_id"],
        unique=True,
        sqlite_where=sa.text("provider_security_id IS NOT NULL"),
        postgresql_where=sa.text("provider_security_id IS NOT NULL"),
    )

    op.create_index(
        op.f("uq_finance_security_figi"),
        "finance_security",
        ["figi"],
        unique=True,
        sqlite_where=sa.text("figi IS NOT NULL"),
        postgresql_where=sa.text("figi IS NOT NULL"),
    )

    op.create_index(
        op.f("uq_finance_security_cusip"),
        "finance_security",
        ["cusip"],
        unique=True,
        sqlite_where=sa.text("cusip IS NOT NULL"),
        postgresql_where=sa.text("cusip IS NOT NULL"),
    )

    op.create_index(
        op.f("uq_finance_security_isin"),
        "finance_security",
        ["isin"],
        unique=True,
        sqlite_where=sa.text("isin IS NOT NULL"),
        postgresql_where=sa.text("isin IS NOT NULL"),
    )

    # Create finance_security_price table
    op.create_table(
        "finance_security_price",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("security_id", sa.Integer(), nullable=False),
        sa.Column("price_date", sa.Date(), nullable=False),
        sa.Column("close_price", sa.BigInteger(), nullable=False),
        sa.Column("price_scale", sa.Integer(), nullable=False, default=2),
        sa.Column("currency", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["security_id"], ["finance_security.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["currency"], ["finance_currency.code"]),
        sa.CheckConstraint(
            "source IN ('plaid', 'snaptrade', 'exchange_api', 'onchain', 'coingecko', 'manual', 'market_data')",
            name="ck_finance_secprice_source",
        ),
    )

    op.create_index(
        op.f("ix_finance_secprice_security_date"),
        "finance_security_price",
        ["security_id", "price_date"],
    )

    op.create_index(
        op.f("uq_finance_secprice"),
        "finance_security_price",
        ["security_id", "price_date", "source"],
        unique=True,
    )

    # Create finance_holding table
    op.create_table(
        "finance_holding",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("security_id", sa.Integer(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("quantity_e8", sa.BigInteger(), nullable=False),
        sa.Column("cost_basis", sa.BigInteger(), nullable=True),
        sa.Column("average_cost", sa.BigInteger(), nullable=True),
        sa.Column("price", sa.BigInteger(), nullable=True),
        sa.Column("price_scale", sa.Integer(), nullable=False, default=2),
        sa.Column("institution_value", sa.BigInteger(), nullable=True),
        sa.Column("vested_quantity_e8", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(16), nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, default={}),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["finance_account.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["security_id"], ["finance_security.id"]),
        sa.ForeignKeyConstraint(["currency"], ["finance_currency.code"]),
    )

    op.create_index(
        op.f("ix_finance_holding_owner"), "finance_holding", ["owner_user_id"]
    )

    op.create_index(
        op.f("ix_finance_holding_account"), "finance_holding", ["account_id"]
    )

    op.create_index(
        op.f("ix_finance_holding_security"), "finance_holding", ["security_id"]
    )

    op.create_index(
        op.f("ix_finance_holding_account_date"),
        "finance_holding",
        ["account_id", "as_of_date"],
    )

    op.create_index(
        op.f("ix_finance_holding_deleted"), "finance_holding", ["deleted_at"]
    )

    op.create_index(
        op.f("uq_finance_holding"),
        "finance_holding",
        ["account_id", "security_id", "as_of_date"],
        unique=True,
    )

    # Create finance_trade table
    op.create_table(
        "finance_trade",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("security_id", sa.Integer(), nullable=True),
        sa.Column("transaction_id", sa.Integer(), nullable=True),
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column("import_batch_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("external_id_source", sa.Text(), nullable=True),
        sa.Column("import_hash", sa.String(64), nullable=True),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("subtype", sa.Text(), nullable=True),
        sa.Column("quantity_e8", sa.BigInteger(), nullable=True),
        sa.Column("price", sa.BigInteger(), nullable=True),
        sa.Column("price_scale", sa.Integer(), nullable=False, default=2),
        sa.Column("amount", sa.BigInteger(), nullable=False, default=0),
        sa.Column("raw_amount", sa.BigInteger(), nullable=True),
        sa.Column("fees", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(16), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("settle_date", sa.Date(), nullable=True),
        sa.Column("datetime", sa.DateTime(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("pending", sa.Boolean(), nullable=False, default=False),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("is_removed", sa.Boolean(), nullable=False, default=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, default={}),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["finance_account.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["security_id"], ["finance_security.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["finance_transaction.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["finance_connection.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["currency"], ["finance_currency.code"]),
        sa.CheckConstraint(
            "source IN ('plaid', 'snaptrade', 'ofx', 'qfx', 'csv', 'manual', 'coinbase', 'onchain')",
            name="ck_finance_trade_source",
        ),
        sa.CheckConstraint(
            "type IN ('buy', 'sell', 'dividend', 'interest', 'fee', 'tax', 'transfer_in', 'transfer_out', 'deposit', 'withdrawal', 'reinvest', 'split', 'cancel', 'other')",
            name="ck_finance_trade_type",
        ),
        sa.CheckConstraint(
            "NOT (external_id IS NOT NULL AND import_hash IS NOT NULL)",
            name="ck_finance_trade_dedup_lane",
        ),
    )

    op.create_index(
        op.f("ix_finance_trade_owner_date"),
        "finance_trade",
        ["owner_user_id", "trade_date"],
    )

    op.create_index(
        op.f("ix_finance_trade_account_date"),
        "finance_trade",
        ["account_id", "trade_date"],
    )

    op.create_index(op.f("ix_finance_trade_security"), "finance_trade", ["security_id"])

    op.create_index(
        op.f("ix_finance_trade_transaction"), "finance_trade", ["transaction_id"]
    )

    op.create_index(
        op.f("ix_finance_trade_connection"), "finance_trade", ["connection_id"]
    )

    op.create_index(
        op.f("ix_finance_trade_batch"), "finance_trade", ["import_batch_id"]
    )

    op.create_index(op.f("ix_finance_trade_deleted"), "finance_trade", ["deleted_at"])

    op.create_index(
        op.f("uq_finance_trade_external"),
        "finance_trade",
        ["account_id", "source", "external_id"],
        unique=True,
        sqlite_where=sa.text("external_id IS NOT NULL AND deleted_at IS NULL"),
        postgresql_where=sa.text("external_id IS NOT NULL AND deleted_at IS NULL"),
    )

    op.create_index(
        op.f("uq_finance_trade_hash"),
        "finance_trade",
        ["account_id", "import_hash"],
        unique=True,
        sqlite_where=sa.text(
            "external_id IS NULL AND import_hash IS NOT NULL AND deleted_at IS NULL"
        ),
        postgresql_where=sa.text(
            "external_id IS NULL AND import_hash IS NOT NULL AND deleted_at IS NULL"
        ),
    )

    # Create finance_recurring_stream table
    op.create_table(
        "finance_recurring_stream",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("merchant_id", sa.Integer(), nullable=True),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column("provider_stream_id", sa.Text(), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("normalized_payee", sa.Text(), nullable=True),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("frequency", sa.String(16), nullable=False),
        sa.Column("average_amount", sa.BigInteger(), nullable=True),
        sa.Column("last_amount", sa.BigInteger(), nullable=True),
        sa.Column("expected_amount", sa.BigInteger(), nullable=True),
        sa.Column("amount_is_variable", sa.Boolean(), nullable=False, default=False),
        sa.Column("amount_tolerance_bps", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(16), nullable=False, default="usd"),
        sa.Column("first_date", sa.Date(), nullable=True),
        sa.Column("last_date", sa.Date(), nullable=True),
        sa.Column("next_expected_date", sa.Date(), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, default=0),
        sa.Column("status", sa.String(16), nullable=False, default="early_detection"),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("is_subscription", sa.Boolean(), nullable=False, default=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, default=True),
        sa.Column("is_user_confirmed", sa.Boolean(), nullable=False, default=False),
        sa.Column("is_muted", sa.Boolean(), nullable=False, default=False),
        sa.Column("paused_until", sa.Date(), nullable=True),
        sa.Column("service_type", sa.Text(), nullable=True),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, default={}),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["finance_account.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id"], ["finance_merchant.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["category_id"], ["finance_category.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["finance_connection.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["currency"], ["finance_currency.code"]),
        sa.ForeignKeyConstraint(["subject_id"], ["finance_subject.id"]),
        sa.CheckConstraint(
            "direction IN ('inflow', 'outflow')", name="ck_finance_recurring_direction"
        ),
        sa.CheckConstraint(
            "frequency IN ('weekly', 'biweekly', 'semi_monthly', 'monthly', 'bimonthly', 'quarterly', 'semi_annually', 'annually', 'once', 'irregular', 'unknown')",
            name="ck_finance_recurring_frequency",
        ),
        sa.CheckConstraint(
            "status IN ('early_detection', 'mature', 'inactive', 'cancelled')",
            name="ck_finance_recurring_status",
        ),
        sa.CheckConstraint(
            "source IN ('plaid', 'derived', 'user')", name="ck_finance_recurring_source"
        ),
    )

    op.create_index(
        op.f("ix_finance_recurring_owner"),
        "finance_recurring_stream",
        ["owner_user_id"],
    )

    op.create_index(
        op.f("ix_finance_recurring_stream_subject_id"),
        "finance_recurring_stream",
        ["subject_id"],
    )

    op.create_index(
        op.f("ix_finance_recurring_account"), "finance_recurring_stream", ["account_id"]
    )

    op.create_index(
        op.f("ix_finance_recurring_merchant"),
        "finance_recurring_stream",
        ["merchant_id"],
    )

    op.create_index(
        op.f("ix_finance_recurring_category"),
        "finance_recurring_stream",
        ["category_id"],
    )

    op.create_index(
        op.f("ix_finance_recurring_connection"),
        "finance_recurring_stream",
        ["connection_id"],
    )

    op.create_index(
        op.f("ix_finance_recurring_next"),
        "finance_recurring_stream",
        ["owner_user_id", "next_expected_date"],
    )

    op.create_index(
        op.f("ix_finance_recurring_status"), "finance_recurring_stream", ["status"]
    )

    op.create_index(
        op.f("ix_finance_recurring_deleted"), "finance_recurring_stream", ["deleted_at"]
    )

    op.create_index(
        op.f("uq_finance_recurring_provider"),
        "finance_recurring_stream",
        ["connection_id", "provider_stream_id"],
        unique=True,
        sqlite_where=sa.text("provider_stream_id IS NOT NULL"),
        postgresql_where=sa.text("provider_stream_id IS NOT NULL"),
    )

    op.create_index(
        op.f("uq_finance_recurring_detected"),
        "finance_recurring_stream",
        ["owner_user_id", "account_id", "direction", "normalized_payee"],
        unique=True,
        sqlite_where=sa.text("provider_stream_id IS NULL"),
        postgresql_where=sa.text("provider_stream_id IS NULL"),
    )

    # Create finance_budget table
    op.create_table(
        "finance_budget",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("period", sa.String(16), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(16), nullable=False, default="usd"),
        sa.Column("philosophy", sa.Text(), nullable=True),
        sa.Column("rollover", sa.Boolean(), nullable=False, default=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, default=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["currency"], ["finance_currency.code"]),
        sa.CheckConstraint(
            "period IN ('monthly', 'weekly', 'quarterly', 'yearly', 'custom')",
            name="ck_finance_budget_period",
        ),
    )

    op.create_index(
        op.f("ix_finance_budget_owner"), "finance_budget", ["owner_user_id"]
    )

    op.create_index(
        op.f("ix_finance_budget_org"), "finance_budget", ["organization_id"]
    )

    op.create_index(op.f("ix_finance_budget_deleted"), "finance_budget", ["deleted_at"])

    op.create_index(
        op.f("uq_finance_budget_owner_name_start"),
        "finance_budget",
        ["owner_user_id", "name", "start_date"],
        unique=True,
    )

    # Create finance_budget_category table
    op.create_table(
        "finance_budget_category",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("budget_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("payee_key", sa.String(96), nullable=True),
        sa.Column("payee_label", sa.String(191), nullable=True),
        sa.Column("period_month", sa.Integer(), nullable=True),
        sa.Column("allocated_amount", sa.BigInteger(), nullable=False, default=0),
        sa.Column("goal_amount", sa.BigInteger(), nullable=True),
        sa.Column("carryover_amount", sa.BigInteger(), nullable=False, default=0),
        sa.Column("rollover_enabled", sa.Boolean(), nullable=False, default=False),
        sa.Column("currency", sa.String(16), nullable=False, default="usd"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["budget_id"], ["finance_budget.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["category_id"], ["finance_category.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["currency"], ["finance_currency.code"]),
        sa.CheckConstraint(
            "category_id IS NULL OR payee_key IS NULL",
            name="ck_finance_budgetcat_target",
        ),
    )

    op.create_index(
        op.f("ix_finance_budgetcat_owner"), "finance_budget_category", ["owner_user_id"]
    )

    op.create_index(
        op.f("ix_finance_budgetcat_budget"), "finance_budget_category", ["budget_id"]
    )

    op.create_index(
        op.f("ix_finance_budgetcat_category"),
        "finance_budget_category",
        ["category_id"],
    )

    op.create_index(
        op.f("ix_finance_budgetcat_month"), "finance_budget_category", ["period_month"]
    )

    op.create_index(
        op.f("uq_finance_budgetcat_category"),
        "finance_budget_category",
        ["budget_id", "category_id", "period_month"],
        unique=True,
        sqlite_where=sa.text("category_id IS NOT NULL"),
        postgresql_where=sa.text("category_id IS NOT NULL"),
    )

    op.create_index(
        op.f("uq_finance_budgetcat_payee"),
        "finance_budget_category",
        ["budget_id", "payee_key", "period_month"],
        unique=True,
        sqlite_where=sa.text("payee_key IS NOT NULL"),
        postgresql_where=sa.text("payee_key IS NOT NULL"),
    )

    op.create_index(
        op.f("uq_finance_budgetcat_overall"),
        "finance_budget_category",
        ["budget_id", "period_month"],
        unique=True,
        sqlite_where=sa.text("category_id IS NULL AND payee_key IS NULL"),
        postgresql_where=sa.text("category_id IS NULL AND payee_key IS NULL"),
    )

    # Create finance_spending_baseline table
    op.create_table(
        "finance_spending_baseline",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("merchant_id", sa.Integer(), nullable=True),
        sa.Column("window_months", sa.Integer(), nullable=False),
        sa.Column("period_month", sa.Integer(), nullable=False),
        sa.Column("trailing_avg_amount", sa.BigInteger(), nullable=False, default=0),
        sa.Column("currency", sa.String(16), nullable=False, default="usd"),
        sa.Column("computed_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["category_id"], ["finance_category.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id"], ["finance_merchant.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["currency"], ["finance_currency.code"]),
        sa.CheckConstraint(
            "window_months IN (3, 6, 12)", name="ck_finance_baseline_window"
        ),
    )

    op.create_index(
        op.f("ix_finance_baseline_owner"),
        "finance_spending_baseline",
        ["owner_user_id"],
    )

    op.create_index(
        op.f("ix_finance_baseline_category"),
        "finance_spending_baseline",
        ["category_id"],
    )

    op.create_index(
        op.f("ix_finance_baseline_merchant"),
        "finance_spending_baseline",
        ["merchant_id"],
    )

    op.create_index(
        op.f("uq_finance_baseline"),
        "finance_spending_baseline",
        [
            "owner_user_id",
            "category_id",
            "merchant_id",
            "window_months",
            "period_month",
        ],
        unique=True,
    )

    # Create finance_insight table
    op.create_table(
        "finance_insight",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("insight_type", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("related_account_id", sa.Integer(), nullable=True),
        sa.Column("related_transaction_id", sa.Integer(), nullable=True),
        sa.Column("related_category_id", sa.Integer(), nullable=True),
        sa.Column("related_stream_id", sa.Integer(), nullable=True),
        sa.Column("detected_amount", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(16), nullable=True),
        sa.Column("dedup_key", sa.Text(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False, default={}),
        sa.Column("status", sa.String(12), nullable=False, default="new"),
        sa.Column("is_read", sa.Boolean(), nullable=False, default=False),
        sa.Column("dismissed_at", sa.DateTime(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, default={}),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["related_account_id"], ["finance_account.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["related_transaction_id"], ["finance_transaction.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["related_category_id"], ["finance_category.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["related_stream_id"], ["finance_recurring_stream.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["currency"], ["finance_currency.code"]),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'critical')",
            name="ck_finance_insight_severity",
        ),
        sa.CheckConstraint(
            "status IN ('new', 'seen', 'dismissed', 'actioned')",
            name="ck_finance_insight_status",
        ),
    )

    op.create_index(
        op.f("ix_finance_insight_owner"), "finance_insight", ["owner_user_id"]
    )

    op.create_index(
        op.f("ix_finance_insight_org"), "finance_insight", ["organization_id"]
    )

    op.create_index(
        op.f("ix_finance_insight_type"), "finance_insight", ["insight_type"]
    )

    op.create_index(op.f("ix_finance_insight_status"), "finance_insight", ["status"])

    op.create_index(
        op.f("ix_finance_insight_account"), "finance_insight", ["related_account_id"]
    )

    op.create_index(
        op.f("ix_finance_insight_transaction"),
        "finance_insight",
        ["related_transaction_id"],
    )

    op.create_index(
        op.f("ix_finance_insight_category"), "finance_insight", ["related_category_id"]
    )

    op.create_index(
        op.f("ix_finance_insight_stream"), "finance_insight", ["related_stream_id"]
    )

    op.create_index(
        op.f("ix_finance_insight_owner_read"),
        "finance_insight",
        ["owner_user_id", "is_read"],
    )

    op.create_index(
        op.f("uq_finance_insight_dedup"),
        "finance_insight",
        ["owner_user_id", "dedup_key"],
        unique=True,
    )

    # Create finance_import_profile table
    op.create_table(
        "finance_import_profile",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("institution_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("source_format", sa.String(8), nullable=False),
        sa.Column("header_signature", sa.JSON(), nullable=False, default={}),
        sa.Column("column_mapping", sa.JSON(), nullable=False, default={}),
        sa.Column("date_format", sa.Text(), nullable=True),
        sa.Column("amount_sign_convention", sa.String(20), nullable=False),
        sa.Column("decimal_separator", sa.String(1), nullable=True),
        sa.Column("thousands_separator", sa.String(1), nullable=True),
        sa.Column("currency", sa.String(16), nullable=False, default="usd"),
        sa.Column("is_system", sa.Boolean(), nullable=False, default=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["institution_id"], ["finance_institution.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["currency"], ["finance_currency.code"]),
        sa.CheckConstraint(
            "source_format IN ('csv', 'ofx', 'qfx', 'qif')",
            name="ck_finance_importprofile_format",
        ),
        sa.CheckConstraint(
            "amount_sign_convention IN ('outflow_negative', 'outflow_positive', 'split_debit_credit')",
            name="ck_finance_importprofile_sign",
        ),
    )

    op.create_index(
        op.f("ix_finance_importprofile_owner"),
        "finance_import_profile",
        ["owner_user_id"],
    )

    op.create_index(
        op.f("ix_finance_importprofile_org"),
        "finance_import_profile",
        ["organization_id"],
    )

    op.create_index(
        op.f("ix_finance_importprofile_institution"),
        "finance_import_profile",
        ["institution_id"],
    )

    op.create_index(
        op.f("ix_finance_importprofile_deleted"),
        "finance_import_profile",
        ["deleted_at"],
    )

    op.create_index(
        op.f("uq_finance_importprofile_owner_name"),
        "finance_import_profile",
        ["owner_user_id", "name"],
        unique=True,
    )

    # Create finance_import_batch table
    op.create_table(
        "finance_import_batch",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("import_profile_id", sa.Integer(), nullable=True),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=True),
        sa.Column("file_sha256", sa.Text(), nullable=True),
        sa.Column("sync_cursor_before", sa.Text(), nullable=True),
        sa.Column("sync_cursor_after", sa.Text(), nullable=True),
        sa.Column("rows_total", sa.Integer(), nullable=False, default=0),
        sa.Column("rows_inserted", sa.Integer(), nullable=False, default=0),
        sa.Column("rows_updated", sa.Integer(), nullable=False, default=0),
        sa.Column("rows_duplicate", sa.Integer(), nullable=False, default=0),
        sa.Column("rows_error", sa.Integer(), nullable=False, default=0),
        sa.Column("status", sa.String(16), nullable=False, default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["finance_connection.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["finance_account.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["import_profile_id"], ["finance_import_profile.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "source_type IN ('plaid_sync', 'snaptrade_sync', 'ofx', 'qfx', 'qif', 'csv', 'manual')",
            name="ck_finance_importbatch_source",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'committed', 'failed', 'rolled_back')",
            name="ck_finance_importbatch_status",
        ),
    )

    op.create_index(
        op.f("ix_finance_importbatch_owner"), "finance_import_batch", ["owner_user_id"]
    )

    op.create_index(
        op.f("ix_finance_importbatch_org"), "finance_import_batch", ["organization_id"]
    )

    op.create_index(
        op.f("ix_finance_importbatch_connection"),
        "finance_import_batch",
        ["connection_id"],
    )

    op.create_index(
        op.f("ix_finance_importbatch_account"), "finance_import_batch", ["account_id"]
    )

    op.create_index(
        op.f("ix_finance_importbatch_profile"),
        "finance_import_batch",
        ["import_profile_id"],
    )

    op.create_index(
        op.f("ix_finance_importbatch_status"), "finance_import_batch", ["status"]
    )

    op.create_index(
        op.f("ix_finance_importbatch_owner_started"),
        "finance_import_batch",
        ["owner_user_id", "started_at"],
    )

    op.create_index(
        op.f("uq_finance_importbatch_file"),
        "finance_import_batch",
        ["owner_user_id", "file_sha256"],
        unique=True,
        sqlite_where=sa.text("file_sha256 IS NOT NULL"),
        postgresql_where=sa.text("file_sha256 IS NOT NULL"),
    )

    # Create finance_import_batch_row table
    op.create_table(
        "finance_import_batch_row",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("import_batch_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("raw_line", sa.Text(), nullable=True),
        sa.Column("parsed", sa.JSON(), nullable=False, default={}),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("fitid", sa.Text(), nullable=True),
        sa.Column("parsed_status", sa.String(12), nullable=False),
        sa.Column("matched_transaction_id", sa.Integer(), nullable=True),
        sa.Column("matched_trade_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["import_batch_id"], ["finance_import_batch.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["finance_account.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["matched_transaction_id"], ["finance_transaction.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["matched_trade_id"], ["finance_trade.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "parsed_status IN ('parsed', 'inserted', 'updated', 'duplicate', 'error', 'matched', 'skipped')",
            name="ck_finance_importrow_status",
        ),
    )

    op.create_index(
        op.f("ix_finance_importrow_batch"),
        "finance_import_batch_row",
        ["import_batch_id"],
    )

    op.create_index(
        op.f("ix_finance_importrow_owner"),
        "finance_import_batch_row",
        ["owner_user_id"],
    )

    op.create_index(
        op.f("ix_finance_importrow_account"), "finance_import_batch_row", ["account_id"]
    )

    op.create_index(
        op.f("ix_finance_importrow_matched_txn"),
        "finance_import_batch_row",
        ["matched_transaction_id"],
    )

    op.create_index(
        op.f("ix_finance_importrow_matched_trade"),
        "finance_import_batch_row",
        ["matched_trade_id"],
    )

    op.create_index(
        op.f("ix_finance_importrow_hash"), "finance_import_batch_row", ["content_hash"]
    )

    op.create_index(
        op.f("uq_finance_importrow_batch_num"),
        "finance_import_batch_row",
        ["import_batch_id", "row_number"],
        unique=True,
    )

    # Create finance_attachment table
    op.create_table(
        "finance_attachment",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("transaction_id", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["finance_transaction.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["finance_account.id"], ondelete="CASCADE"
        ),
    )

    op.create_index(
        op.f("ix_finance_attachment_owner"), "finance_attachment", ["owner_user_id"]
    )

    op.create_index(
        op.f("ix_finance_attachment_org"), "finance_attachment", ["organization_id"]
    )

    op.create_index(
        op.f("ix_finance_attachment_transaction"),
        "finance_attachment",
        ["transaction_id"],
    )

    op.create_index(
        op.f("ix_finance_attachment_account"), "finance_attachment", ["account_id"]
    )

    op.create_index(
        op.f("ix_finance_attachment_deleted"), "finance_attachment", ["deleted_at"]
    )

    op.create_index(
        op.f("uq_finance_attachment_owner_sha"),
        "finance_attachment",
        ["owner_user_id", "sha256"],
        unique=True,
        sqlite_where=sa.text("sha256 IS NOT NULL"),
        postgresql_where=sa.text("sha256 IS NOT NULL"),
    )

    # Create finance_analyst_snapshot table
    op.create_table(
        "finance_analyst_snapshot",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("net_worth", sa.BigInteger(), nullable=True),
        sa.Column("cash_today", sa.BigInteger(), nullable=True),
        sa.Column("portfolio_total", sa.BigInteger(), nullable=True),
        sa.Column("positions", sa.Integer(), nullable=False, default=0),
        sa.Column("projection_end_date", sa.Date(), nullable=True),
        sa.Column("projection_end_amount", sa.BigInteger(), nullable=True),
        sa.Column("projection_low_date", sa.Date(), nullable=True),
        sa.Column("projection_low_amount", sa.BigInteger(), nullable=True),
        sa.Column("first_negative_date", sa.Date(), nullable=True),
        sa.Column("first_negative_name", sa.String(255), nullable=True),
        sa.Column("open_critical", sa.Integer(), nullable=False, default=0),
        sa.Column("open_warning", sa.Integer(), nullable=False, default=0),
        sa.Column("goals_total_saved", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_finance_analyst_snapshot_owner"),
        "finance_analyst_snapshot",
        ["owner_user_id"],
    )

    op.create_index(
        op.f("ix_finance_analyst_snapshot_day"),
        "finance_analyst_snapshot",
        ["as_of_date"],
    )

    op.create_index(
        op.f("uq_finance_analyst_snapshot_owner_day"),
        "finance_analyst_snapshot",
        ["owner_user_id", "as_of_date"],
        unique=True,
    )

    # Create finance_transaction_changelog table
    op.create_table(
        "finance_transaction_changelog",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("change_source", sa.Text(), nullable=False),
        sa.Column("sync_cursor", sa.Text(), nullable=True),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["finance_transaction.id"], ondelete="CASCADE"
        ),
    )

    op.create_index(
        op.f("ix_finance_changelog_transaction"),
        "finance_transaction_changelog",
        ["transaction_id"],
    )

    op.create_index(
        op.f("ix_finance_changelog_owner"),
        "finance_transaction_changelog",
        ["owner_user_id"],
    )

    op.create_index(
        op.f("ix_finance_changelog_changed"),
        "finance_transaction_changelog",
        ["changed_at"],
    )

    # Alter finance_transaction table — batch_alter_table is required for
    # SQLite, which doesn't support ALTER for FK constraints. Postgres
    # treats it as plain ALTER, so this is portable across both backends.
    # Drops run before adds so names can be reused (e.g. swap an index
    # from one column set to another with the same name).
    with op.batch_alter_table("finance_transaction") as batch_op:
        batch_op.create_foreign_key(
            "fk_finance_transaction_transfer_group_id_finance_transfer",
            "finance_transfer",
            ["transfer_group_id"],
            ["id"],
            ondelete="SET NULL",
        )

        batch_op.create_foreign_key(
            "fk_finance_transaction_category_id_finance_category",
            "finance_category",
            ["category_id"],
            ["id"],
            ondelete="SET NULL",
        )

        batch_op.create_foreign_key(
            "fk_finance_transaction_merchant_id_finance_merchant",
            "finance_merchant",
            ["merchant_id"],
            ["id"],
            ondelete="SET NULL",
        )

        batch_op.create_foreign_key(
            "fk_finance_txn_recurring_stream",
            "finance_recurring_stream",
            ["recurring_stream_id"],
            ["id"],
            ondelete="SET NULL",
        )

        batch_op.create_foreign_key(
            "fk_finance_txn_import_batch",
            "finance_import_batch",
            ["import_batch_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # Alter finance_transaction_split table — batch_alter_table is required for
    # SQLite, which doesn't support ALTER for FK constraints. Postgres
    # treats it as plain ALTER, so this is portable across both backends.
    # Drops run before adds so names can be reused (e.g. swap an index
    # from one column set to another with the same name).
    with op.batch_alter_table("finance_transaction_split") as batch_op:
        batch_op.create_foreign_key(
            "fk_finance_transaction_split_category_id_finance_category",
            "finance_category",
            ["category_id"],
            ["id"],
            ondelete="SET NULL",
        )

        batch_op.create_foreign_key(
            "fk_finance_transaction_split_merchant_id_finance_merchant",
            "finance_merchant",
            ["merchant_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # Alter finance_trade table — batch_alter_table is required for
    # SQLite, which doesn't support ALTER for FK constraints. Postgres
    # treats it as plain ALTER, so this is portable across both backends.
    # Drops run before adds so names can be reused (e.g. swap an index
    # from one column set to another with the same name).
    with op.batch_alter_table("finance_trade") as batch_op:
        batch_op.create_foreign_key(
            "fk_finance_trade_import_batch",
            "finance_import_batch",
            ["import_batch_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    """Reverse finance migration."""

    with op.batch_alter_table("finance_trade") as batch_op:
        batch_op.drop_constraint("fk_finance_trade_import_batch", type_="foreignkey")

    with op.batch_alter_table("finance_transaction_split") as batch_op:
        batch_op.drop_constraint(
            "fk_finance_transaction_split_merchant_id_finance_merchant",
            type_="foreignkey",
        )

        batch_op.drop_constraint(
            "fk_finance_transaction_split_category_id_finance_category",
            type_="foreignkey",
        )

    with op.batch_alter_table("finance_transaction") as batch_op:
        batch_op.drop_constraint("fk_finance_txn_import_batch", type_="foreignkey")

        batch_op.drop_constraint("fk_finance_txn_recurring_stream", type_="foreignkey")

        batch_op.drop_constraint(
            "fk_finance_transaction_merchant_id_finance_merchant", type_="foreignkey"
        )

        batch_op.drop_constraint(
            "fk_finance_transaction_category_id_finance_category", type_="foreignkey"
        )

        batch_op.drop_constraint(
            "fk_finance_transaction_transfer_group_id_finance_transfer",
            type_="foreignkey",
        )

    op.drop_index(
        op.f("ix_finance_changelog_transaction"),
        table_name="finance_transaction_changelog",
    )

    op.drop_index(
        op.f("ix_finance_changelog_owner"), table_name="finance_transaction_changelog"
    )

    op.drop_index(
        op.f("ix_finance_changelog_changed"), table_name="finance_transaction_changelog"
    )

    op.drop_table("finance_transaction_changelog")

    op.drop_index(
        op.f("ix_finance_analyst_snapshot_owner"), table_name="finance_analyst_snapshot"
    )

    op.drop_index(
        op.f("ix_finance_analyst_snapshot_day"), table_name="finance_analyst_snapshot"
    )

    op.drop_index(
        op.f("uq_finance_analyst_snapshot_owner_day"),
        table_name="finance_analyst_snapshot",
    )

    op.drop_table("finance_analyst_snapshot")

    op.drop_index(op.f("ix_finance_attachment_owner"), table_name="finance_attachment")

    op.drop_index(op.f("ix_finance_attachment_org"), table_name="finance_attachment")

    op.drop_index(
        op.f("ix_finance_attachment_transaction"), table_name="finance_attachment"
    )

    op.drop_index(
        op.f("ix_finance_attachment_account"), table_name="finance_attachment"
    )

    op.drop_index(
        op.f("ix_finance_attachment_deleted"), table_name="finance_attachment"
    )

    op.drop_index(
        op.f("uq_finance_attachment_owner_sha"), table_name="finance_attachment"
    )

    op.drop_table("finance_attachment")

    op.drop_index(
        op.f("ix_finance_importrow_batch"), table_name="finance_import_batch_row"
    )

    op.drop_index(
        op.f("ix_finance_importrow_owner"), table_name="finance_import_batch_row"
    )

    op.drop_index(
        op.f("ix_finance_importrow_account"), table_name="finance_import_batch_row"
    )

    op.drop_index(
        op.f("ix_finance_importrow_matched_txn"), table_name="finance_import_batch_row"
    )

    op.drop_index(
        op.f("ix_finance_importrow_matched_trade"),
        table_name="finance_import_batch_row",
    )

    op.drop_index(
        op.f("ix_finance_importrow_hash"), table_name="finance_import_batch_row"
    )

    op.drop_index(
        op.f("uq_finance_importrow_batch_num"), table_name="finance_import_batch_row"
    )

    op.drop_table("finance_import_batch_row")

    op.drop_index(
        op.f("ix_finance_importbatch_owner"), table_name="finance_import_batch"
    )

    op.drop_index(op.f("ix_finance_importbatch_org"), table_name="finance_import_batch")

    op.drop_index(
        op.f("ix_finance_importbatch_connection"), table_name="finance_import_batch"
    )

    op.drop_index(
        op.f("ix_finance_importbatch_account"), table_name="finance_import_batch"
    )

    op.drop_index(
        op.f("ix_finance_importbatch_profile"), table_name="finance_import_batch"
    )

    op.drop_index(
        op.f("ix_finance_importbatch_status"), table_name="finance_import_batch"
    )

    op.drop_index(
        op.f("ix_finance_importbatch_owner_started"), table_name="finance_import_batch"
    )

    op.drop_index(
        op.f("uq_finance_importbatch_file"), table_name="finance_import_batch"
    )

    op.drop_table("finance_import_batch")

    op.drop_index(
        op.f("ix_finance_importprofile_owner"), table_name="finance_import_profile"
    )

    op.drop_index(
        op.f("ix_finance_importprofile_org"), table_name="finance_import_profile"
    )

    op.drop_index(
        op.f("ix_finance_importprofile_institution"),
        table_name="finance_import_profile",
    )

    op.drop_index(
        op.f("ix_finance_importprofile_deleted"), table_name="finance_import_profile"
    )

    op.drop_index(
        op.f("uq_finance_importprofile_owner_name"), table_name="finance_import_profile"
    )

    op.drop_table("finance_import_profile")

    op.drop_index(op.f("ix_finance_insight_owner"), table_name="finance_insight")

    op.drop_index(op.f("ix_finance_insight_org"), table_name="finance_insight")

    op.drop_index(op.f("ix_finance_insight_type"), table_name="finance_insight")

    op.drop_index(op.f("ix_finance_insight_status"), table_name="finance_insight")

    op.drop_index(op.f("ix_finance_insight_account"), table_name="finance_insight")

    op.drop_index(op.f("ix_finance_insight_transaction"), table_name="finance_insight")

    op.drop_index(op.f("ix_finance_insight_category"), table_name="finance_insight")

    op.drop_index(op.f("ix_finance_insight_stream"), table_name="finance_insight")

    op.drop_index(op.f("ix_finance_insight_owner_read"), table_name="finance_insight")

    op.drop_index(op.f("uq_finance_insight_dedup"), table_name="finance_insight")

    op.drop_table("finance_insight")

    op.drop_index(
        op.f("ix_finance_baseline_owner"), table_name="finance_spending_baseline"
    )

    op.drop_index(
        op.f("ix_finance_baseline_category"), table_name="finance_spending_baseline"
    )

    op.drop_index(
        op.f("ix_finance_baseline_merchant"), table_name="finance_spending_baseline"
    )

    op.drop_index(op.f("uq_finance_baseline"), table_name="finance_spending_baseline")

    op.drop_table("finance_spending_baseline")

    op.drop_index(
        op.f("ix_finance_budgetcat_owner"), table_name="finance_budget_category"
    )

    op.drop_index(
        op.f("ix_finance_budgetcat_budget"), table_name="finance_budget_category"
    )

    op.drop_index(
        op.f("ix_finance_budgetcat_category"), table_name="finance_budget_category"
    )

    op.drop_index(
        op.f("ix_finance_budgetcat_month"), table_name="finance_budget_category"
    )

    op.drop_index(
        op.f("uq_finance_budgetcat_category"), table_name="finance_budget_category"
    )

    op.drop_index(
        op.f("uq_finance_budgetcat_payee"), table_name="finance_budget_category"
    )

    op.drop_index(
        op.f("uq_finance_budgetcat_overall"), table_name="finance_budget_category"
    )

    op.drop_table("finance_budget_category")

    op.drop_index(op.f("ix_finance_budget_owner"), table_name="finance_budget")

    op.drop_index(op.f("ix_finance_budget_org"), table_name="finance_budget")

    op.drop_index(op.f("ix_finance_budget_deleted"), table_name="finance_budget")

    op.drop_index(
        op.f("uq_finance_budget_owner_name_start"), table_name="finance_budget"
    )

    op.drop_table("finance_budget")

    op.drop_index(
        op.f("ix_finance_recurring_owner"), table_name="finance_recurring_stream"
    )

    op.drop_index(
        op.f("ix_finance_recurring_stream_subject_id"),
        table_name="finance_recurring_stream",
    )

    op.drop_index(
        op.f("ix_finance_recurring_account"), table_name="finance_recurring_stream"
    )

    op.drop_index(
        op.f("ix_finance_recurring_merchant"), table_name="finance_recurring_stream"
    )

    op.drop_index(
        op.f("ix_finance_recurring_category"), table_name="finance_recurring_stream"
    )

    op.drop_index(
        op.f("ix_finance_recurring_connection"), table_name="finance_recurring_stream"
    )

    op.drop_index(
        op.f("ix_finance_recurring_next"), table_name="finance_recurring_stream"
    )

    op.drop_index(
        op.f("ix_finance_recurring_status"), table_name="finance_recurring_stream"
    )

    op.drop_index(
        op.f("ix_finance_recurring_deleted"), table_name="finance_recurring_stream"
    )

    op.drop_index(
        op.f("uq_finance_recurring_provider"), table_name="finance_recurring_stream"
    )

    op.drop_index(
        op.f("uq_finance_recurring_detected"), table_name="finance_recurring_stream"
    )

    op.drop_table("finance_recurring_stream")

    op.drop_index(op.f("ix_finance_trade_owner_date"), table_name="finance_trade")

    op.drop_index(op.f("ix_finance_trade_account_date"), table_name="finance_trade")

    op.drop_index(op.f("ix_finance_trade_security"), table_name="finance_trade")

    op.drop_index(op.f("ix_finance_trade_transaction"), table_name="finance_trade")

    op.drop_index(op.f("ix_finance_trade_connection"), table_name="finance_trade")

    op.drop_index(op.f("ix_finance_trade_batch"), table_name="finance_trade")

    op.drop_index(op.f("ix_finance_trade_deleted"), table_name="finance_trade")

    op.drop_index(op.f("uq_finance_trade_external"), table_name="finance_trade")

    op.drop_index(op.f("uq_finance_trade_hash"), table_name="finance_trade")

    op.drop_table("finance_trade")

    op.drop_index(op.f("ix_finance_holding_owner"), table_name="finance_holding")

    op.drop_index(op.f("ix_finance_holding_account"), table_name="finance_holding")

    op.drop_index(op.f("ix_finance_holding_security"), table_name="finance_holding")

    op.drop_index(op.f("ix_finance_holding_account_date"), table_name="finance_holding")

    op.drop_index(op.f("ix_finance_holding_deleted"), table_name="finance_holding")

    op.drop_index(op.f("uq_finance_holding"), table_name="finance_holding")

    op.drop_table("finance_holding")

    op.drop_index(
        op.f("ix_finance_secprice_security_date"), table_name="finance_security_price"
    )

    op.drop_index(op.f("uq_finance_secprice"), table_name="finance_security_price")

    op.drop_table("finance_security_price")

    op.drop_index(op.f("ix_finance_security_ticker"), table_name="finance_security")

    op.drop_index(op.f("ix_finance_security_cusip"), table_name="finance_security")

    op.drop_index(op.f("ix_finance_security_isin"), table_name="finance_security")

    op.drop_index(
        op.f("ix_finance_security_provider_secid"), table_name="finance_security"
    )

    op.drop_index(op.f("ix_finance_security_type"), table_name="finance_security")

    op.drop_index(op.f("uq_finance_security_provider"), table_name="finance_security")

    op.drop_index(op.f("uq_finance_security_figi"), table_name="finance_security")

    op.drop_index(op.f("uq_finance_security_cusip"), table_name="finance_security")

    op.drop_index(op.f("uq_finance_security_isin"), table_name="finance_security")

    op.drop_table("finance_security")

    op.drop_index(op.f("ix_finance_rule_owner"), table_name="finance_rule")

    op.drop_index(op.f("ix_finance_rule_org"), table_name="finance_rule")

    op.drop_index(op.f("ix_finance_rule_owner_priority"), table_name="finance_rule")

    op.drop_index(op.f("ix_finance_rule_deleted"), table_name="finance_rule")

    op.drop_table("finance_rule")

    op.drop_index(op.f("ix_finance_txntag_tag"), table_name="finance_transaction_tag")

    op.drop_index(op.f("ix_finance_txntag_split"), table_name="finance_transaction_tag")

    op.drop_table("finance_transaction_tag")

    op.drop_index(op.f("ix_finance_tag_owner"), table_name="finance_tag")

    op.drop_index(op.f("ix_finance_tag_org"), table_name="finance_tag")

    op.drop_index(op.f("ix_finance_tag_deleted"), table_name="finance_tag")

    op.drop_index(op.f("uq_finance_tag_owner_name"), table_name="finance_tag")

    op.drop_table("finance_tag")

    op.drop_index(op.f("ix_finance_merchant_owner"), table_name="finance_merchant")

    op.drop_index(op.f("ix_finance_merchant_org"), table_name="finance_merchant")

    op.drop_index(op.f("ix_finance_merchant_normalized"), table_name="finance_merchant")

    op.drop_index(
        op.f("ix_finance_merchant_default_cat"), table_name="finance_merchant"
    )

    op.drop_index(op.f("ix_finance_merchant_deleted"), table_name="finance_merchant")

    op.drop_index(op.f("uq_finance_merchant_global"), table_name="finance_merchant")

    op.drop_index(op.f("uq_finance_merchant_user"), table_name="finance_merchant")

    op.drop_index(op.f("uq_finance_merchant_provider"), table_name="finance_merchant")

    op.drop_table("finance_merchant")

    op.drop_index(
        op.f("ix_finance_catalias_owner"), table_name="finance_category_alias"
    )

    op.drop_index(
        op.f("ix_finance_catalias_category"), table_name="finance_category_alias"
    )

    op.drop_index(
        op.f("ix_finance_catalias_normalized"), table_name="finance_category_alias"
    )

    op.drop_index(
        op.f("uq_finance_catalias_owner_norm"), table_name="finance_category_alias"
    )

    op.drop_table("finance_category_alias")

    op.drop_index(op.f("ix_finance_category_owner"), table_name="finance_category")

    op.drop_index(op.f("ix_finance_category_parent"), table_name="finance_category")

    op.drop_index(op.f("ix_finance_category_pfc"), table_name="finance_category")

    op.drop_index(
        op.f("uq_finance_category_system_slug"), table_name="finance_category"
    )

    op.drop_index(op.f("uq_finance_category_user_slug"), table_name="finance_category")

    op.drop_table("finance_category")

    op.drop_index(op.f("ix_finance_transfer_owner"), table_name="finance_transfer")

    op.drop_index(
        op.f("ix_finance_transfer_from_account"), table_name="finance_transfer"
    )

    op.drop_index(op.f("ix_finance_transfer_to_account"), table_name="finance_transfer")

    op.drop_index(op.f("ix_finance_transfer_from_txn"), table_name="finance_transfer")

    op.drop_index(op.f("ix_finance_transfer_to_txn"), table_name="finance_transfer")

    op.drop_index(op.f("ix_finance_transfer_group_key"), table_name="finance_transfer")

    op.drop_index(op.f("uq_finance_transfer_from"), table_name="finance_transfer")

    op.drop_index(op.f("uq_finance_transfer_to"), table_name="finance_transfer")

    op.drop_table("finance_transfer")

    op.drop_index(
        op.f("ix_finance_split_parent"), table_name="finance_transaction_split"
    )

    op.drop_index(
        op.f("ix_finance_split_owner"), table_name="finance_transaction_split"
    )

    op.drop_index(
        op.f("ix_finance_split_category"), table_name="finance_transaction_split"
    )

    op.drop_index(
        op.f("ix_finance_split_merchant"), table_name="finance_transaction_split"
    )

    op.drop_index(
        op.f("uq_finance_split_parent_sort"), table_name="finance_transaction_split"
    )

    op.drop_table("finance_transaction_split")

    op.drop_index(op.f("ix_finance_txn_owner_date"), table_name="finance_transaction")

    op.drop_index(op.f("ix_finance_txn_account_date"), table_name="finance_transaction")

    op.drop_index(
        op.f("ix_finance_txn_owner_cat_date"), table_name="finance_transaction"
    )

    op.drop_index(op.f("ix_finance_txn_merchant"), table_name="finance_transaction")

    op.drop_index(
        op.f("ix_finance_txn_merchant_entity"), table_name="finance_transaction"
    )

    op.drop_index(op.f("ix_finance_txn_category"), table_name="finance_transaction")

    op.drop_index(op.f("ix_finance_txn_connection"), table_name="finance_transaction")

    op.drop_index(op.f("ix_finance_txn_batch"), table_name="finance_transaction")

    op.drop_index(op.f("ix_finance_txn_recurring"), table_name="finance_transaction")

    op.drop_index(
        op.f("ix_finance_txn_transfer_group"), table_name="finance_transaction"
    )

    op.drop_index(op.f("ix_finance_txn_canonical"), table_name="finance_transaction")

    op.drop_index(op.f("ix_finance_txn_pending_link"), table_name="finance_transaction")

    op.drop_index(op.f("ix_finance_txn_pair"), table_name="finance_transaction")

    op.drop_index(op.f("ix_finance_txn_reverses"), table_name="finance_transaction")

    op.drop_index(op.f("ix_finance_txn_pending"), table_name="finance_transaction")

    op.drop_index(op.f("ix_finance_txn_is_transfer"), table_name="finance_transaction")

    op.drop_index(op.f("ix_finance_txn_deleted"), table_name="finance_transaction")

    op.drop_index(op.f("uq_finance_txn_external"), table_name="finance_transaction")

    op.drop_index(op.f("uq_finance_txn_hash"), table_name="finance_transaction")

    op.drop_table("finance_transaction")

    op.drop_index(
        op.f("ix_finance_valuation_owner_date"), table_name="finance_valuation"
    )

    op.drop_index(
        op.f("ix_finance_valuation_account_date"), table_name="finance_valuation"
    )

    op.drop_index(op.f("ix_finance_valuation_source"), table_name="finance_valuation")

    op.drop_index(op.f("uq_finance_valuation"), table_name="finance_valuation")

    op.drop_table("finance_valuation")

    op.drop_index(
        op.f("ix_finance_networth_owner_date"), table_name="finance_net_worth_snapshot"
    )

    op.drop_index(
        op.f("ix_finance_networth_org_date"), table_name="finance_net_worth_snapshot"
    )

    op.drop_index(op.f("uq_finance_networth"), table_name="finance_net_worth_snapshot")

    op.drop_table("finance_net_worth_snapshot")

    op.drop_index(
        op.f("ix_finance_balsnap_account_date"), table_name="finance_balance_snapshot"
    )

    op.drop_index(
        op.f("ix_finance_balsnap_owner_date"), table_name="finance_balance_snapshot"
    )

    op.drop_index(op.f("uq_finance_balsnap"), table_name="finance_balance_snapshot")

    op.drop_table("finance_balance_snapshot")

    op.drop_index(op.f("ix_finance_pending_owner"), table_name="finance_pending_change")

    op.drop_index(
        op.f("ix_finance_pending_status"), table_name="finance_pending_change"
    )

    op.drop_index(
        op.f("ix_finance_pending_change_batch_id"), table_name="finance_pending_change"
    )

    op.drop_table("finance_pending_change")

    op.drop_index(
        op.f("ix_finance_liability_owner"), table_name="finance_liability_detail"
    )

    op.drop_index(
        op.f("ix_finance_liability_account"), table_name="finance_liability_detail"
    )

    op.drop_index(
        op.f("uq_finance_liability_account"), table_name="finance_liability_detail"
    )

    op.drop_table("finance_liability_detail")

    op.drop_index(op.f("ix_finance_account_owner"), table_name="finance_account")

    op.drop_index(op.f("ix_finance_account_subject_id"), table_name="finance_account")

    op.drop_index(op.f("ix_finance_account_org"), table_name="finance_account")

    op.drop_index(op.f("ix_finance_account_connection"), table_name="finance_account")

    op.drop_index(op.f("ix_finance_account_institution"), table_name="finance_account")

    op.drop_index(op.f("ix_finance_account_persistent"), table_name="finance_account")

    op.drop_index(op.f("ix_finance_account_deleted"), table_name="finance_account")

    op.drop_index(op.f("ix_finance_account_owner_type"), table_name="finance_account")

    op.drop_index(
        op.f("ix_finance_account_owner_classification"), table_name="finance_account"
    )

    op.drop_index(op.f("uq_finance_account_provider"), table_name="finance_account")

    op.drop_table("finance_account")

    op.drop_index(op.f("ix_finance_subject_owner"), table_name="finance_subject")

    op.drop_table("finance_subject")

    op.drop_index(
        op.f("ix_finance_webhook_connection"), table_name="finance_webhook_event"
    )

    op.drop_index(op.f("ix_finance_webhook_item"), table_name="finance_webhook_event")

    op.drop_index(
        op.f("ix_finance_webhook_status_received"), table_name="finance_webhook_event"
    )

    op.drop_index(op.f("uq_finance_webhook_event"), table_name="finance_webhook_event")

    op.drop_table("finance_webhook_event")

    op.drop_index(op.f("ix_finance_connection_owner"), table_name="finance_connection")

    op.drop_index(op.f("ix_finance_connection_org"), table_name="finance_connection")

    op.drop_index(
        op.f("ix_finance_connection_institution"), table_name="finance_connection"
    )

    op.drop_index(
        op.f("ix_finance_connection_needs_action"), table_name="finance_connection"
    )

    op.drop_index(
        op.f("ix_finance_connection_deleted"), table_name="finance_connection"
    )

    op.drop_index(
        op.f("ix_finance_connection_owner_status"), table_name="finance_connection"
    )

    op.drop_index(
        op.f("uq_finance_connection_provider_item"), table_name="finance_connection"
    )

    op.drop_index(op.f("uq_finance_connection_wallet"), table_name="finance_connection")

    op.drop_table("finance_connection")

    op.drop_index(
        op.f("ix_finance_institution_provider"), table_name="finance_institution"
    )

    op.drop_index(op.f("ix_finance_institution_name"), table_name="finance_institution")

    op.drop_index(
        op.f("uq_finance_institution_provider_extid"), table_name="finance_institution"
    )

    op.drop_table("finance_institution")

    op.drop_index(op.f("ix_finance_icon_domain"), table_name="finance_icon")

    op.drop_table("finance_icon")

    op.drop_index(op.f("ix_finance_fxrate_pair_date"), table_name="finance_fx_rate")

    op.drop_index(op.f("uq_finance_fxrate"), table_name="finance_fx_rate")

    op.drop_table("finance_fx_rate")

    op.drop_index(op.f("ix_finance_currency_code"), table_name="finance_currency")

    op.drop_index(op.f("ix_finance_currency_kind"), table_name="finance_currency")

    op.drop_table("finance_currency")
