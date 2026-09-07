"""Shared dataclasses for the ops adapters.

No business logic — just the wire shapes both protocol sides agree on.
Frozen dataclasses (not Pydantic models) because these are internal
plumbing types not exposed over HTTP; cheaper, simpler, no validation
overhead. JSON-serialisable via ``dataclasses.asdict`` so the audit
writer doesn't need a custom encoder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class DnsRecord:
    """A DNS record the mail provider wants the registrar to create.

    ``host`` is the relative host (e.g. ``"send.app"`` for
    ``send.app.example.com``). Each registrar adapter is responsible for
    joining or stripping the parent domain to whatever shape its API
    expects. ``ttl`` is advisory; registrars often clamp to their own
    minimum (Porkbun's floor is 600s as of writing).
    """

    host: str
    type: str  # "TXT" / "CNAME" / "MX" / ...
    value: str
    ttl: int = 3600
    # Optional priority used by MX records. Most adapters ignore it for
    # TXT/CNAME. Stored as int | None so JSON-asdict keeps it explicit.
    priority: int | None = None


@dataclass(frozen=True)
class RetrievedRecord:
    """An existing DNS record returned by ``list_dns_records``.

    Mirrors ``DnsRecord`` but adds ``provider_id`` so the CLI can pass
    it back to ``delete_dns_record``. Registrars that don't issue stable
    ids leave ``provider_id`` as an empty string and the caller falls
    back to ``(host, type, value)`` matching.
    """

    provider_id: str
    host: str
    type: str
    value: str
    ttl: int
    priority: int | None = None


@dataclass(frozen=True)
class CreatedRecord:
    """A record that's been provisioned at the registrar.

    Carries the spec back (``record``) for audit-file readability and
    the registrar's own id (``provider_id``) so future delete / update
    calls can target the row precisely. Some registrars don't return a
    stable id; in that case ``provider_id`` is an empty string and the
    adapter falls back to matching by ``(host, type, value)``.
    """

    record: DnsRecord
    provider_id: str
    created_at: datetime


@dataclass(frozen=True)
class DomainAddResult:
    """What the mail provider tells us when we add a domain.

    ``required_records`` is the list the registrar needs to create
    before ``wait_for_verification`` will succeed. ``provider_domain_id``
    is the mail provider's stable handle for this domain — used to
    poll status without re-resolving by string each tick.
    """

    domain: str
    required_records: list[DnsRecord]
    provider_domain_id: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DomainStatus:
    """One snapshot of the mail provider's view of the domain."""

    domain: str
    verified: bool
    status: str  # "pending" / "verified" / "failed" / vendor-specific
    last_checked_at: datetime
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ForwardingRule:
    """A registrar-managed forward, e.g. hello@ -> personal@gmail.com."""

    domain: str
    local_part: str
    target: str
    provider_id: str


@dataclass(frozen=True)
class EmailSetupResult:
    """Final output of ``setup_email_domain`` — also what gets audited.

    Everything the operator (and future-them, and any agent reading the
    project) needs to answer "what did we do, when, and how do we undo it"
    lives on this one object.

    ``warnings`` carries human-readable breadcrumbs for steps that
    soft-failed (e.g. the registrar's email-forwarding feature isn't
    enabled, so we skipped that step but otherwise finished cleanly).
    The CLI renders each entry under the final-status block; the audit
    file persists them so future-you can answer "why is there no
    forwarding rule?" six months from now.
    """

    domain: str
    mail_provider: str  # adapter.name
    registrar: str  # adapter.name
    created_records: list[CreatedRecord]
    forwarding_rule: ForwardingRule | None
    final_status: DomainStatus
    suggested_env_value: str  # e.g. "Acme <hello@app.example.com>"
    started_at: datetime
    finished_at: datetime
    dry_run: bool = False
    warnings: list[str] = field(default_factory=list)
