"""Email-domain setup orchestrator.

Composes a ``MailProviderAdapter`` (Resend) and a ``RegistrarAdapter``
(Porkbun) into one end-to-end flow:

    1. Tell the mail provider about the domain → get required DNS records.
    2. Create those records at the registrar.
    3. Poll the mail provider until the domain is verified.
    4. (Optional) Create an email-forwarding rule at the registrar.
    5. Write an audit file capturing what we did.
    6. Return the suggested ``RESEND_FROM_EMAIL`` value the operator
       should paste into ``.env``.

This module is **deliberately ignorant of which vendors are in use** —
it talks to the protocols, not the adapters. The CLI command picks the
concrete adapters; this orchestrator wires them together.
"""

from __future__ import annotations

from datetime import UTC, datetime
import logging

from app.services.ops.audit import write_audit
from app.services.ops.protocols import MailProviderAdapter, RegistrarAdapter
from app.services.ops.types import (
    CreatedRecord,
    EmailSetupResult,
    ForwardingRule,
)

logger = logging.getLogger(__name__)


def parent_domain(domain: str) -> str:
    """Return the registrar-side parent of ``domain``.

    Porkbun (and most registrars) want DNS records keyed by the
    **registered** domain — ``example.com`` — with subdomains encoded
    in the record's ``name`` field. So when the mail-side domain is
    ``app.example.com``, the registrar API call goes against
    ``example.com``.

    Implementation: if the domain has >2 labels, drop the leftmost
    label and return the rest. Two-label domains (``example.com``,
    ``example.org``) are already registered domains; return as-is.
    The naive split works for almost every realistic registered
    domain — the gnarly cases (``co.uk``, ``com.au``, etc.) need a
    public-suffix-list lookup, but we'll cross that bridge when a
    user actually hits it.
    """
    labels = domain.strip(".").split(".")
    if len(labels) <= 2:
        return domain
    return ".".join(labels[1:])


def build_sender_string(sender_name: str, local_part: str, domain: str) -> str:
    """Render the ``RESEND_FROM_EMAIL`` value we suggest the operator paste.

    Format: ``"Display Name <local@domain>"`` — the Resend SDK parses
    this natively and inboxes render only the display name, which is
    the brand/trust signal users react to.
    """
    sender = (sender_name or "").strip()
    addr = f"{local_part.strip()}@{domain.strip()}"
    return f"{sender} <{addr}>" if sender else addr


async def setup_email_domain(
    *,
    domain: str,
    registrar: RegistrarAdapter,
    mail_provider: MailProviderAdapter,
    sender_name: str = "Aegis",
    local_part: str = "hello",
    forward_to: str | None = None,
    timeout_s: int = 600,
    poll_interval_s: int = 5,
    dry_run: bool = False,
    write_audit_file: bool = True,
) -> EmailSetupResult:
    """Provision ``domain`` end-to-end against the given adapters.

    Args:
        domain: The sending domain — typically a subdomain of a domain
            you own, e.g. ``app.example.com``. The registrar adapter
            is called against ``parent_domain(domain)``.
        registrar: ``RegistrarAdapter`` for the DNS side (e.g.
            ``PorkbunAdapter``).
        mail_provider: ``MailProviderAdapter`` for the sending side
            (e.g. ``ResendAdapter``).
        sender_name: Display name in the rendered FROM string.
        local_part: Local part of the suggested FROM address.
        forward_to: If set, also creates a registrar-managed email-
            forwarding rule (``local_part@domain`` → ``forward_to``).
            Cloudflare and Porkbun both support this.
        timeout_s: How long ``wait_for_verification`` may spin.
        poll_interval_s: How often it polls within that window.
        dry_run: Skip every external mutation. Useful for sanity-
            checking adapter wiring without provisioning anything.
            The mail provider is still asked for the required records
            so the caller can see what *would* be created.
        write_audit_file: If True (default), writes the result to
            ``.aegis/email-setup.json`` under the project root.

    Returns:
        ``EmailSetupResult`` with everything the audit file captures.
    """
    started_at = datetime.now(UTC)
    registrar_domain = parent_domain(domain)

    logger.info(
        "ops.email_setup: starting for %s (parent=%s, dry_run=%s)",
        domain,
        registrar_domain,
        dry_run,
    )

    # Step 1 — ask the mail provider what records to create.
    add_result = await mail_provider.add_domain(domain)
    logger.info(
        "ops.email_setup: %s requires %d DNS records",
        mail_provider.name,
        len(add_result.required_records),
    )

    # Step 2 — provision records at the registrar (skipped under dry-run).
    created_records: list[CreatedRecord]
    if dry_run:
        now = datetime.now(UTC)
        created_records = [
            CreatedRecord(record=spec, provider_id="(dry-run)", created_at=now)
            for spec in add_result.required_records
        ]
    else:
        created_records = await registrar.create_dns_records(
            registrar_domain, add_result.required_records
        )

    # Step 3 — poll until verified (skipped under dry-run).
    if dry_run:
        from app.services.ops.types import DomainStatus

        final_status = DomainStatus(
            domain=domain,
            verified=False,
            status="(dry-run)",
            last_checked_at=datetime.now(UTC),
        )
    else:
        final_status = await mail_provider.wait_for_verification(
            domain, timeout_s=timeout_s, poll_interval_s=poll_interval_s
        )

    # Step 4 — optional email forwarding. Three branches:
    # - dry-run                         -> synthesize a (dry-run) rule
    # - registrar supports forwarding   -> attempt the API call
    # - registrar doesn't                -> skip with an honest note
    #
    # Some registrars (Porkbun today) expose email forwarding only via
    # the web UI. The orchestrator probes a ``supports_email_forwarding``
    # capability method via ``getattr`` so adapters can omit it and
    # default to True; explicit ``return False`` short-circuits cleanly.
    forwarding_rule: ForwardingRule | None = None
    warnings: list[str] = []
    if forward_to:
        if dry_run:
            forwarding_rule = ForwardingRule(
                domain=registrar_domain,
                local_part=local_part,
                target=forward_to,
                provider_id="(dry-run)",
            )
        else:
            supports_check = getattr(registrar, "supports_email_forwarding", None)
            supports = supports_check() if callable(supports_check) else True
            if not supports:
                warnings.append(
                    f"Email forwarding ({registrar.name}): no API support. "
                    f"If you want replies to {local_part}@{registrar_domain} "
                    f"routed to {forward_to}, set up the rule manually in "
                    f"the registrar's web UI. The rest of this setup is "
                    "unaffected."
                )
            else:
                from app.services.ops.adapters.porkbun import (
                    PorkbunEmailForwardingNotSupportedError,
                )

                try:
                    forwarding_rule = await registrar.create_email_forward(
                        registrar_domain, local_part, forward_to
                    )
                except PorkbunEmailForwardingNotSupportedError as exc:
                    warnings.append(f"Email forwarding skipped: {exc}")

    finished_at = datetime.now(UTC)
    result = EmailSetupResult(
        domain=domain,
        mail_provider=mail_provider.name,
        registrar=registrar.name,
        created_records=created_records,
        forwarding_rule=forwarding_rule,
        final_status=final_status,
        suggested_env_value=build_sender_string(sender_name, local_part, domain),
        started_at=started_at,
        finished_at=finished_at,
        dry_run=dry_run,
        warnings=warnings,
    )

    if write_audit_file:
        path = write_audit(result)
        logger.info("ops.email_setup: audit written to %s", path)

    return result
