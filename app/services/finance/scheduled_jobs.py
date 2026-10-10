"""The finance service's scheduled jobs (``app.core.schedule``)."""

from app.core.schedule import LONG_RUNNING, ServiceJob
from app.services.finance.jobs import (
    finance_bill_due_email_job,
    finance_envelope_credit_job,
    finance_goal_auto_contribute_job,
    finance_recompute_snapshots_job,
    finance_sync_connections_job,
)

JOBS: tuple[ServiceJob, ...] = (
    # Net-worth engine: materialize per-account balance and per-user
    # net-worth snapshots nightly, so the chart is a cheap range scan.
    ServiceJob(
        finance_recompute_snapshots_job,
        "finance_recompute_snapshots",
        "Finance: Recompute Net-Worth Snapshots",
        {"trigger": "cron", "hour": 2},
        timeout=LONG_RUNNING,
    ),
    # Goals: book toggled-on goals' declared amounts on the 1st. Idempotent
    # per month, so a missed run caught up later books nothing twice.
    ServiceJob(
        finance_goal_auto_contribute_job,
        "finance_goal_auto_contribute",
        "Finance: Auto-Contribute to Goals",
        {"trigger": "cron", "day": 1, "hour": 2, "minute": 45},
    ),
    # Envelopes: the allowance arrives on its cadence (weekly credits land
    # Mondays), so the job checks daily and its per-period idempotency
    # decides whether anything books.
    ServiceJob(
        finance_envelope_credit_job,
        "finance_envelope_credit",
        "Finance: Credit Envelopes",
        {"trigger": "cron", "hour": 2, "minute": 50},
    ),
    # Keep linked banks fresh between logins; webhooks handle the real-time
    # nudge when a public URL is configured.
    ServiceJob(
        finance_sync_connections_job,
        "finance_sync_connections",
        "Finance: Sync Bank Connections",
        {"trigger": "interval", "hours": 6},
        timeout=LONG_RUNNING,
    ),
    # Morning, after the overnight passes: what is due in the next few
    # days, mailed once. Does nothing until FINANCE_BILL_EMAIL_TO is set.
    ServiceJob(
        finance_bill_due_email_job,
        "finance_bill_due_email",
        "Finance: Email Bills Coming Due",
        {"trigger": "cron", "hour": 7, "minute": 0},
    ),
    # No nightly analyst note: steward writes it on request (#232).
)
