"""The service standard, held by the parts pattern's report
(``service_parts.PARTS``): every enforced rule at zero breaches, but for
the reads outside queries modules recorded below as debt.

Behavioural tests cannot see structure: a read that drifts out of its
package's ``queries`` module still returns the same rows. This is where
the standard fails on the drift. Likewise a subsystem left loose beside
a service's standard files instead of in a package under ``domains/``.
"""

from app.core import ownership, source_index
from app.services.system import pattern_findings, service_parts

READS = "parts.reads-in-queries"
# Files each part still reads in outside its queries modules: a ratchet,
# like ``test_module_size_budget.BUDGET``. A count may only drop, and a
# part not listed is held at zero; lower an entry as its reads move.
READS_DEBT = {
    "component:scheduler": 3,
    "service:ai": 5,
    "service:auth": 8,
    "service:documents": 6,
    "service:documents.mail": 2,
    "service:documents.matters": 11,
    "service:finance": 6,
    "service:finance.insurance": 1,
    "shared:system": 1,
}
LOOSE = "parts.domains"
# Modules each service still keeps loose beside its standard files, held
# the same way: lower an entry as its modules move into domains/.
LOOSE_DEBT = {
    "service:comms": 2,
    "service:finance": 6,
}
FACADE = "parts.through-facade"
# Files each part imports another service's internals from, rather than
# its facade, held the same way: lower an entry as finance's and
# documents' facades offer what they reach for (#505).
FACADE_DEBT = {
    "service:ai": 3,
    "service:documents": 10,
    "service:documents.mail": 1,
    "service:documents.matters": 6,
    "service:finance": 8,
}
JOBS = "parts.jobs-wired"
# Jobs modules each part keeps a job in that nothing runs.
JOBS_DEBT = {
    "service:finance": 1,
}

# Files outside a service that import its internals (``service_parts.
# INTERNALS``) rather than its facade. The parts rule holds a service's own
# folder; this holds everything else (components, the CLI, core, shared
# code), the same ratchet: a file may only leave the list.
REACHING_DEBT = frozenset(
    {
        "app/cli/finance_snaptrade.py",
        "app/components/backend/api/llm/routes.py",
        "app/components/backend/shutdown/finance_webhook_tunnel.py",
        "app/components/backend/startup/database_init.py",
        "app/components/backend/startup/finance_webhook_tunnel.py",
        "app/components/backend/startup/live_engine.py",
        "app/components/frontend/dashboard/modals/documents_activity.py",
        "app/components/frontend/dashboard/modals/documents_detail_pane.py",
        "app/components/frontend/dashboard/modals/documents_modal.py",
        "app/components/frontend/dashboard/modals/finance_modal/budget_panel/header.py",
        "app/components/frontend/dashboard/modals/finance_modal/budget_panel/lines_tab.py",
        "app/components/frontend/dashboard/modals/finance_modal/connect.py",
        "app/components/frontend/dashboard/modals/finance_modal/dialog.py",
        "app/components/frontend/dashboard/modals/finance_modal/formatting.py",
        "app/components/frontend/dashboard/modals/finance_modal/transactions_panel/imports_target.py",
        "app/components/web_frontend/app_globals.py",
        "app/components/web_frontend/documents.py",
        "app/components/web_frontend/filters.py",
        "app/components/web_frontend/routes/chat.py",
        "app/components/web_frontend/routes/chat_changes.py",
        "app/components/web_frontend/routes/chat_live.py",
        "app/components/web_frontend/routes/chat_models.py",
        "app/components/web_frontend/routes/chat_speech.py",
        "app/components/web_frontend/routes/contacts.py",
        "app/components/web_frontend/routes/documents.py",
        "app/components/web_frontend/routes/facts.py",
        "app/components/web_frontend/routes/filed.py",
        "app/components/web_frontend/routes/finance/account_manage.py",
        "app/components/web_frontend/routes/finance/account_merge.py",
        "app/components/web_frontend/routes/finance/account_naming.py",
        "app/components/web_frontend/routes/finance/accounts.py",
        "app/components/web_frontend/routes/finance/bank_attention.py",
        "app/components/web_frontend/routes/finance/bank_marks.py",
        "app/components/web_frontend/routes/finance/bills.py",
        "app/components/web_frontend/routes/finance/bills_match.py",
        "app/components/web_frontend/routes/finance/budget.py",
        "app/components/web_frontend/routes/finance/budget_display.py",
        "app/components/web_frontend/routes/finance/budget_envelopes.py",
        "app/components/web_frontend/routes/finance/budget_goals.py",
        "app/components/web_frontend/routes/finance/changes_log.py",
        "app/components/web_frontend/routes/finance/connect_providers.py",
        "app/components/web_frontend/routes/finance/connection_placing.py",
        "app/components/web_frontend/routes/finance/cover.py",
        "app/components/web_frontend/routes/finance/imports.py",
        "app/components/web_frontend/routes/finance/institutions.py",
        "app/components/web_frontend/routes/finance/overview.py",
        "app/components/web_frontend/routes/finance/portfolio.py",
        "app/components/web_frontend/routes/finance/positions.py",
        "app/components/web_frontend/routes/finance/projected.py",
        "app/components/web_frontend/routes/finance/register.py",
        "app/components/web_frontend/routes/finance/register_views.py",
        "app/components/web_frontend/routes/finance/reimbursables.py",
        "app/components/web_frontend/routes/finance/review.py",
        "app/components/web_frontend/routes/finance/review_edit.py",
        "app/components/web_frontend/routes/finance/review_resolve.py",
        "app/components/web_frontend/routes/finance/settings.py",
        "app/components/web_frontend/routes/finance/settings_comms.py",
        "app/components/web_frontend/routes/finance/subjects.py",
        "app/components/web_frontend/routes/finance/transactions.py",
        "app/components/web_frontend/routes/finance/transfer_pairs.py",
        "app/components/web_frontend/routes/finance/valuations.py",
        "app/components/web_frontend/routes/icons.py",
        "app/components/web_frontend/routes/matter_papers.py",
        "app/components/web_frontend/routes/matter_timeline.py",
        "app/components/web_frontend/routes/matters.py",
        "app/components/web_frontend/routes/partials/overseer_documents.py",
        "app/components/web_frontend/routes/policies.py",
        "app/components/web_frontend/routes/request_items.py",
        "app/components/web_frontend/routes/requests.py",
        "app/components/worker/tasks/document_tasks.py",
        "app/components/worker/tasks/finance_tasks.py",
        "app/services/ai/domains/chat/attachments.py",
        "app/services/ai/domains/chat/pastes.py",
        "app/services/ai/fixtures/__init__.py",
        "app/services/documents/domains/reading/banks.py",
        "app/services/documents/domains/reading/changes.py",
        "app/services/documents/domains/reading/check_card.py",
        "app/services/documents/domains/reading/checks.py",
        "app/services/documents/domains/reading/figures.py",
        "app/services/documents/domains/reading/identity.py",
        "app/services/documents/domains/reading/joins.py",
        "app/services/documents/domains/reading/proposals.py",
        "app/services/documents/domains/reading/receipts.py",
        "app/services/documents/domains/reading/statements.py",
        "app/services/finance/domains/detection/analyst/prompt_changes.py",
        "app/services/finance/domains/imports_job.py",
        "app/services/finance/domains/ledger/queries/accounts.py",
        "app/services/finance/domains/writes/combining.py",
        "app/services/finance/domains/writes/executors.py",
        "app/services/finance/domains/writes/filing.py",
        "app/services/finance/domains/writes/queue.py",
        "app/services/finance/jobs.py",
        "app/services/mail/ingest.py",
        "app/services/matters/changes.py",
        "app/services/matters/deadlines.py",
        "app/services/matters/facts.py",
        "app/services/matters/ledger_figures.py",
        "app/services/matters/lookup.py",
        "app/services/matters/tools.py",
        "app/services/system/rekey.py",
    }
)


def _findings() -> list[pattern_findings.Finding]:
    return pattern_findings.findings(service_parts.report())


def _errors() -> list[pattern_findings.Finding]:
    return [f for f in _findings() if f.severity == "error"]


def _past(check: str, debt: dict[str, int]) -> dict[str, list[str]]:
    """The files each subject breaks ``check`` in, where past its debt."""
    files = {f.subject: sorted(set(f.files)) for f in _findings() if f.check == check}
    return {s: found for s, found in files.items() if len(found) > debt.get(s, 0)}


def test_reads_outside_queries_never_grow() -> None:
    over = _past(READS, READS_DEBT)
    assert not over, f"reads outside queries modules, past the debt: {over}"


def test_loose_modules_never_grow() -> None:
    over = _past(LOOSE, LOOSE_DEBT)
    assert not over, f"modules left loose beside the standard, past the debt: {over}"


def test_facade_breaches_never_grow() -> None:
    over = _past(FACADE, FACADE_DEBT)
    assert not over, f"internals imported past a facade, past the debt: {over}"


def test_unwired_jobs_never_grow() -> None:
    over = _past(JOBS, JOBS_DEBT)
    assert not over, f"jobs nothing runs, past the debt: {over}"


def test_every_other_enforced_rule_holds() -> None:
    held = {READS, FACADE, JOBS}
    broken = [
        (f.check, f.subject, f.evidence) for f in _errors() if f.check not in held
    ]
    assert not broken, broken


def _service_of_internal(target: str, index: source_index.Index) -> str | None:
    """The service whose internals ``target`` (a dotted module or name) is,
    else None."""
    words = target.split(".")
    if words[:2] != ["app", "services"] or len(words) < 4:
        return None
    if words[3] not in service_parts.INTERNALS:
        return None
    found = ownership.owner(target, index)
    return found.name if found and found.kind == ownership.SERVICE else None


def test_no_caller_outside_a_service_reaches_past_its_facade() -> None:
    index = source_index.project()
    reaching = set()
    for name in index.paths:
        module = index.get(name) if name.startswith("app.") else None
        if module is None:
            continue
        mine = ownership.owner(name, index)
        for source, member in module.imported.values():
            target = f"{source}.{member}" if member else source
            theirs = _service_of_internal(target, index)
            if theirs and (mine is None or mine.name != theirs):
                reaching.add(module.path)
    assert reaching <= REACHING_DEBT, sorted(reaching - REACHING_DEBT)
