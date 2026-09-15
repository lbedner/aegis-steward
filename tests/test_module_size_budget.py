"""Module size budget.

The reader this protects is an agent. A file is the unit it loads, greps and
edits: 500 lines of this codebase is roughly 7k tokens, so ten fit in a
working window alongside everything else. A 2,000-line file eats a fifth of
that window by itself and pushes past the Read tool's default window, which
turns "read the file" into "page the file" - and paging is where whole-file
reasoning starts making mistakes.

Two rules, because one number cannot express the shape:

* **Logic modules cap at 500 lines.** The number ``docs/plan.md`` has carried
  since Phase 1, where Pulse's 1825-line ``pages.py`` is named as the
  anti-pattern. It was written down and never enforced, so seven web frontend
  route modules passed it without anything noticing.
* **Declaration modules get 900.** Models, schemas and locale catalogues are
  lists, not arguments. Nobody reads them end to end - they get grepped for
  one name - so length costs far less.

BUDGET is a RATCHET, not an allowlist. Each entry records a file's size when
it was measured, and the test fails if the file grows past its own entry. So
existing debt does not block work, nothing gets worse, and anything NEW must
meet the budget outright.

Ported from aegis-stack's ``tests/core/test_module_size_budget.py``, which
guards the template the same way.
"""

from __future__ import annotations

from pathlib import Path

APP = Path(__file__).parent.parent / "app"

LOGIC_LIMIT = 500
DECLARATION_LIMIT = 900

# Lists rather than logic: read by grep, never end to end.
DECLARATION_PARTS = ("models", "schemas", "i18n", "seeds", "fixtures", "locales")

# path -> line count when recorded. The refactoring backlog, largest first.
BUDGET: dict[str, int] = {
    "cli/ai.py": 2171,
    "components/frontend/dashboard/modals/modal_sections.py": 2093,
    "components/frontend/dashboard/modals/worker_modal.py": 1351,
    "i18n/locales/en.py": 1301,
    "i18n/locales/zh.py": 1277,
    "i18n/locales/ja.py": 1247,
    "i18n/locales/ru.py": 1247,
    "i18n/locales/fr.py": 1247,
    "i18n/locales/es.py": 1247,
    "i18n/locales/de.py": 1247,
    "i18n/locales/ko.py": 1246,
    "components/frontend/dashboard/modals/backend_modal.py": 1244,
    "i18n/locales/zh_hant.py": 1243,
    "services/finance/adapters/importers/imports.py": 1231,
    "components/frontend/dashboard/modals/ollama_modal.py": 1202,
    "components/frontend/controls/data_table.py": 1082,
    "components/frontend/main.py": 1047,
    "services/finance/adapters/providers/connections/plaid_sync.py": 1039,
    "services/system/health.py": 988,
    "services/finance/seeds/demo_seed.py": 953,
    "services/finance/domains/detection/insights/rules.py": 944,
    "components/web_frontend/routes/chat.py": 936,
    "components/frontend/dashboard/modals/finance_modal/transactions_panel/panel.py": 899,
    "components/frontend/dashboard/modals/finance_modal/uncategorized_panel.py": 850,
    "components/web_frontend/routes/finance/account_manage.py": 839,
    "components/frontend/dashboard/modals/comms_modal.py": 832,
    "components/frontend/controls/form_fields.py": 810,
    "cli/load_test.py": 805,
    "components/web_frontend/routes/finance/bills.py": 786,
    "components/web_frontend/routes/finance/transactions.py": 781,
    "components/frontend/dashboard/modals/llm_catalog_tab.py": 776,
    "services/ai/domains/llm/etl/llm_sync_service.py": 737,
    "cli/finance.py": 720,
    "components/web_frontend/routes/finance/settings.py": 712,
    "components/frontend/dashboard/modals/finance_modal/overview_tab.py": 703,
    "services/load_test/worker/service.py": 679,
    "cli/health.py": 674,
    "components/frontend/dashboard/modals/finance_modal/no_payee_panel.py": 668,
    "components/frontend/dashboard/cards/card_utils.py": 646,
    "components/frontend/dashboard/modals/redis_modal.py": 645,
    "components/frontend/dashboard/modals/database_modal.py": 630,
    "components/frontend/controls/pickers.py": 629,
    "services/ai/domains/llm/providers.py": 603,
    "services/ai/domains/chat/health_context.py": 597,
    "cli/api_load_test.py": 591,
    "services/finance/domains/detection/recurring/detect.py": 591,
    "components/frontend/dashboard/modals/finance_modal/transactions_panel/declare.py": 586,
    "components/backend/startup/component_health.py": 583,
    "services/finance/domains/ledger/queries/transactions.py": 581,
    "components/frontend/dashboard/modals/finance_payees_tab.py": 576,
    "components/frontend/dashboard/modals/finance_modal/budget_cards.py": 575,
    "components/frontend/dashboard/activity_feed.py": 572,
    "services/finance/domains/ledger/merchants.py": 570,
    "components/worker/task_history.py": 569,
    "components/frontend/theme.py": 566,
    "components/backend/api/ai/router.py": 565,
    "services/finance/domains/planning/budgets/summary.py": 563,
    "services/system/health_db_sqlite.py": 550,
    "core/config.py": 544,
    "services/finance/domains/planning/goals.py": 544,
    "cli/marko_terminal_renderer.py": 535,
    "components/frontend/dashboard/modals/finance_modal/budget_panel/panel.py": 534,
    "services/finance/domains/ledger/networth.py": 531,
    "components/frontend/dashboard/modals/ai_analytics_tab.py": 530,
    "services/finance/domains/ledger/accounts.py": 521,
    # The change-type vocabulary the agent is taught lives in here and
    # grows by a line or two with every new type. Re-recorded when
    # account.create learned whose money an account holds; the file is
    # on the list to split - the vocabulary is its own document.
    "services/finance/domains/detection/analyst/prompts.py": 529,
    "cli/llm.py": 512,
    "services/finance/domains/detection/transfers.py": 511,
    "services/finance/adapters/providers/connections/snaptrade_sync.py": 504,
    "components/backend/api/worker.py": 501,
}


def _is_declaration(rel: str) -> bool:
    return any(part in rel.split("/") for part in DECLARATION_PARTS)


def _limit_for(rel: str) -> int:
    return DECLARATION_LIMIT if _is_declaration(rel) else LOGIC_LIMIT


def _python_files() -> dict[str, int]:
    return {
        str(p.relative_to(APP)): len(p.read_text().splitlines())
        for p in APP.rglob("*.py")
        if "__pycache__" not in str(p)
    }


def test_no_module_exceeds_its_line_budget() -> None:
    over = []
    for rel, lines in sorted(_python_files().items()):
        limit = BUDGET.get(rel) or _limit_for(rel)
        if lines > limit:
            how = "over its recorded size" if rel in BUDGET else "over budget"
            over.append(f"{rel}: {lines} lines, {how} ({limit})")
    assert not over, "Split these, or the budget is a suggestion:\n  " + "\n  ".join(
        over
    )


def test_the_backlog_has_no_dead_entries() -> None:
    """A file that has shrunk under its limit loses its entry.

    Otherwise the backlog only ever grows, and a recorded size becomes a
    licence rather than a debt.
    """
    sizes = _python_files()
    dead = [
        f"{rel}: now {sizes[rel]} lines, under the {_limit_for(rel)} limit"
        for rel in BUDGET
        if rel in sizes and sizes[rel] <= _limit_for(rel)
    ]
    assert not dead, "Delete these BUDGET lines:\n  " + "\n  ".join(dead)


def test_the_backlog_names_real_files() -> None:
    """A moved or deleted file leaves its entry behind, quietly widening
    the budget for a path nothing occupies."""
    sizes = _python_files()
    missing = [rel for rel in BUDGET if rel not in sizes]
    assert not missing, "These BUDGET paths no longer exist:\n  " + "\n  ".join(missing)
