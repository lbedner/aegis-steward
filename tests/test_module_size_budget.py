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
    "i18n/locales/en.py": 1324,
    "i18n/locales/zh.py": 1300,
    "i18n/locales/de.py": 1270,
    "i18n/locales/es.py": 1270,
    "i18n/locales/fr.py": 1270,
    "i18n/locales/ja.py": 1270,
    "i18n/locales/ru.py": 1270,
    "i18n/locales/ko.py": 1269,
    "i18n/locales/zh_hant.py": 1266,
    "components/frontend/controls/data_table.py": 1082,
    "components/frontend/controls/data_table/table.py": 746,
    "components/frontend/main.py": 1047,
    "services/finance/adapters/providers/connections/plaid_sync.py": 1039,
    "services/finance/seeds/demo_seed.py": 953,
    "services/finance/domains/detection/insights/rules.py": 831,
    "components/web_frontend/routes/chat.py": 931,
    "components/frontend/dashboard/modals/comms_modal.py": 832,
    "components/web_frontend/routes/finance/transactions.py": 781,
    "services/finance/adapters/importers/imports.py": 811,
    "cli/finance.py": 697,
    "components/web_frontend/routes/finance/settings.py": 693,
    "components/web_frontend/routes/finance/account_manage.py": 675,
    "components/frontend/dashboard/cards/card_utils.py": 646,
    "components/frontend/dashboard/modals/redis_modal.py": 645,
    "components/web_frontend/routes/finance/bills.py": 634,
    "components/frontend/dashboard/modals/database_modal.py": 630,
    "services/finance/domains/detection/recurring/detect.py": 591,
    "services/finance/domains/ledger/queries/transactions.py": 581,
    "services/finance/domains/ledger/merchants.py": 570,
    "services/finance/domains/planning/budgets/summary.py": 563,
    "services/system/health_db_sqlite.py": 550,
    "core/config.py": 586,
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
