"""The stamp signatures: proof-objects for every shipped migration.

The startup hook re-adopts a persisted database by checking, per pending
migration (``NNN_<service>.py``), whether its signature object already
exists - and stamping instead of replaying DDL. A migration that ships
WITHOUT an entry opts out of that recovery and re-creates the
finance_icon incident: alembic_version lags, the upgrade replays, and the
database logs "already exists" on every boot until someone stamps by
hand. Guarded by ``tests/test_migration_signatures.py``.

Forms:
- ``("table", name)`` - table exists (CREATE TABLE migrations). Names may
  be schema-qualified; matching tolerates the bare name on engines
  without schemas (SQLite).
- ``("column", table, col)`` - column exists (ALTER TABLE migrations).
- ``("foreign_key", table, col)`` - an FK constraint covers the column
  (FK-only migrations, e.g. payment_auth_link).
- ``("check", table, name)`` - a named CHECK constraint exists with the
  migration's text in it. For a migration that WIDENS a constraint and
  adds no object: there is no new table or column to point at, and the
  constraint is the only thing whose shape proves the migration ran.
"""

SERVICE_MIGRATION_SIGNATURES: dict[str, tuple[str, ...]] = {
    "ai": ("table", "llm_org"),
    "ai_agents": ("table", "agent"),
    "agent_code_mode": ("column", "agent", "code_mode"),
    "ai_sentiment": ("table", "sentiment_analysis"),
    "ai_voice": ("table", "voice_usage"),
    "auth": ("table", "user"),
    "auth_org": ("table", "organization"),
    "auth_rbac": ("column", "user", "role"),
    "auth_tokens": ("table", "refresh_token"),
    "blog": ("table", "blog_post"),
    "documents": ("table", "document"),
    "evidence_link": ("table", "evidence_link"),
    "matter_event": ("table", "matter_event"),
    # Had a model and no migration for months; create_all built it at
    # startup on every install, which is exactly the habit #163 removes.
    # Existing databases adopt it here instead of replaying the DDL.
    "job_execution": ("table", "job_execution"),
    # Widens ck_document_kind; adds no table or column.
    "document_schedule": ("check", "document", "ck_document_kind", "schedule"),
    "payoff_terms": (
        "column",
        "finance.finance_liability_detail",
        "prepayment_penalty",
    ),
    "run_detail": ("column", "finance.finance_import_batch", "detail"),
    "arrival": ("column", "finance.finance_holding", "import_batch_id"),
    "party": ("table", "party"),
    "matter": ("table", "matter"),
    "request": ("table", "request"),
    "request_item_document": ("column", "request_item", "document_id"),
    "fact": ("table", "fact"),
    "fact_source_url": ("column", "fact", "source_url"),
    "sign_in": ("table", "sign_in"),
    "place": ("column", "fact", "source_party_id"),
    "subject_party": ("column", "finance.finance_subject", "party_id"),
    "institution_party": (
        "column",
        "finance.finance_institution",
        "party_id",
    ),
    "fact_account": ("column", "fact", "account_id"),
    "item_kind": ("column", "request_item", "kind"),
    "prompt_fingerprint": ("column", "agent", "prompt_fingerprint"),
    "insurance": ("table", "insurance_policy"),
    "claim_paid": ("column", "insurance_claim", "paid_transaction_id"),
    "account_identity": ("column", "finance.finance_institution", "routing_number"),
    # insight_source, not project: the project table only exists in the
    # per-user shape, insight_source in both.
    "insights": ("table", "insight_source"),
    "payment": ("table", "payment_provider"),
    "payment_auth_link": ("foreign_key", "payment_customer", "user_id"),
    "finance_subjects": ("table", "finance.finance_subject"),
    "finance": ("table", "finance.finance_account"),
    "finance_budget_payee": ("column", "finance.finance_budget_category", "payee_key"),
    "finance_auth_link": ("foreign_key", "finance.finance_account", "owner_user_id"),
    "finance_icon": ("table", "finance.finance_icon"),
    "transaction_logo": ("column", "finance.finance_transaction", "logo_url"),
    "merchant_alias": ("table", "finance.finance_merchant_alias"),
    "institution_owner": (
        "column",
        "finance.finance_institution",
        "owner_user_id",
    ),
    "pending_changes": ("table", "finance.finance_pending_change"),
    "pending_change_batch": (
        "column",
        "finance.finance_pending_change",
        "batch_id",
    ),
    "secured_debt": (
        "column",
        "finance.finance_liability_detail",
        "secured_by_account_id",
    ),
    "scheduler": ("table", "scheduler.job_execution"),
}
