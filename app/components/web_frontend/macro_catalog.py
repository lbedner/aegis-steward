"""The shared macros as data: every public macro in the macro files, with
its signature and the comment above it read from the source, and an
example that is both shown and rendered, so the code on screen is the
code that produced the preview beside it.

The Overseer's Patterns page renders it; a test fails when a public macro
has no example here.
"""

from functools import lru_cache
import re
from textwrap import dedent
from typing import NamedTuple

from markupsafe import Markup

from app.core.log import logger

from .rendering import templates

FILES = {
    "layout": "components/macros/layout.html",
    "feedback": "components/macros/feedback.html",
    "form": "components/macros/form.html",
    "table": "components/macros/table.html",
}

# Macros that act on the whole page (a second #dialog or toast region, an
# out-of-band swap, a live request) are shown as code only.
CODE_ONLY = frozenset(
    {
        "dialog",
        "drawer",
        "drawer_sync",
        "confirm",
        "toast_region",
        "oob",
        "pager",
        "sse_job",
        "confirm_permanent",
    }
)

# An optional comment directly above ``{% macro name(...) %}``.
_MACRO = re.compile(
    r"(?:\{#(?P<doc>(?:(?!#\}).)*)#\}\s*)?"
    r"\{%-?\s*macro\s+(?P<signature>(?P<name>\w+)\(.*?\))\s*-?%\}",
    re.DOTALL,
)

EXAMPLES: dict[str, dict[str, str]] = {
    "layout": {
        "sidebar_icon": """
            <span class="inline-flex gap-3 text-aegis-muted">
              {{ sidebar_icon("database") }}{{ sidebar_icon("worker") }}
              {{ sidebar_icon("scheduler") }}{{ sidebar_icon("cache") }}
            </span>""",
        "card": """
            {% call card("Queue health", subtitle="Last hour") %}
              <p class="text-sm text-aegis-muted">Any content goes in the body.</p>
            {% endcall %}""",
        "stat_tile": """
            <dl class="grid grid-cols-2 gap-3">
              {{ stat_tile("Waiting", "1,204") }}
              {{ stat_tile("Net rate", "-18/min", negative=True, caption="Falling behind") }}
            </dl>""",
        "progress": """
            <div class="space-y-3">
              {{ progress(0.35, label="Used") }}
              {{ progress(0.8, "warn", size="h-2") }}
              {{ progress(0.97, "error", size="h-2") }}
            </div>""",
        "resource_bar": """
            {% set memory = {"metadata": {"percent_used": 62.5}, "status": {"value": "healthy"}} %}
            {{ resource_bar("Memory", memory, "2.5 GB of 4 GB") }}""",
        "chart_panel": """
            {{ chart_panel("catalog-demo", "Jobs per hour", "bar", {
              "labels": ["09:00", "10:00", "11:00", "12:00"],
              "series": [{"label": "Done", "values": [120, 180, 150, 210]}],
            }) }}""",
        "chart": """
            {{ chart("catalog-bare", "line", {
              "labels": ["Mon", "Tue", "Wed"],
              "series": [{"label": "Calls", "values": [4, 9, 6]}],
              "format": "count",
            }, height="h-40", label="Calls this week") }}""",
        "chart_data": """
            {# What a live stream re-sends for chart_panel(..., live=...). #}
            {{ chart_data("catalog-demo", {"labels": [1, 2], "series": [{"label": "Done", "values": [3, 4]}]}) }}""",
        "dialog": """
            {# Mounted once by base.html; content arrives via hx_dialog(...). #}
            {{ dialog() }}""",
        "drawer": """
            {# Mounted once by base.html; a list opens it with drawer_sync. #}
            {{ drawer() }}""",
        "drawer_sync": """
            {# In a list: the open item's partial, or none to close it. #}
            {{ drawer_sync("/partials/overseer/documents/12/drawer", "document") }}""",
        "avatar": """
            <span class="inline-flex items-center gap-3">
              {{ avatar("Anthropic") }} {{ avatar("OpenAI") }} {{ avatar("Groq") }}
            </span>""",
        "theme_toggle": "{{ theme_toggle() }}",
        "badge": """
            <span class="flex flex-wrap gap-4 text-sm">
              {{ badge("Healthy", "ok") }} {{ badge("Degraded", "warn") }}
              {{ badge("Failing", "error") }} {{ badge("Idle", "muted") }}
              {{ badge("Active", "accent") }}
            </span>""",
        "status_dot": """
            <span class="flex items-center gap-3">
              {{ status_dot("healthy") }} {{ status_dot("warning") }}
              {{ status_dot("unhealthy") }}
            </span>""",
        "health_badge": """
            <span class="flex flex-wrap items-center gap-4 text-sm">
              {{ health_badge("healthy") }} {{ health_badge("warning") }}
              {{ health_badge("unhealthy") }}
            </span>""",
        "menu_item": """
            {% set on_click %}@click="$dispatch('toast', { text: 'Clicked', tone: 'ok' })"{% endset %}
            {% call dropdown("Actions", align="left") %}
              {{ menu_item("Retry", on_click) }}
              {{ menu_item("Delete", "", danger=True) }}
            {% endcall %}""",
        "dropdown": """
            {% set on_click %}@click="$dispatch('toast', { text: 'Clicked', tone: 'ok' })"{% endset %}
            {% call dropdown("Actions", align="left") %}
              {{ menu_item("Retry", on_click) }}
              {{ menu_item("Pause", "") }}
            {% endcall %}""",
        "dialog_title": """
            {{ dialog_title("Remove user", "They lose access at once. Their history stays.") }}""",
        "confirm": """
            {{ confirm("Remove user?", "They lose access at once.", "/api/v1/users/42") }}""",
        "page_header": """
            {% call page_header("Worker", "3 queues, all healthy") %}
              {{ figures([{"label": "Waiting", "value": "1,204"}]) }}
            {% endcall %}""",
        "figures": """
            {{ figures([
              {"label": "Done today", "value": "18,420"},
              {"label": "Failed", "value": "12", "negative": True},
            ]) }}""",
        "stats_strip": """
            {{ stats_strip([
              {"label": "Waiting", "value": "1,204", "caption": "oldest 3 min"},
              {"label": "Running", "value": "48"},
              {"label": "Done", "value": "18,420", "tone": "ok"},
            ]) }}""",
        "ranked_rows": """
            {{ ranked_rows([
              {"label": "load_test", "count": "3 workers", "value": "1,204", "ratio": 1.0},
              {"label": "system", "count": "1 worker", "value": "310", "ratio": 0.26},
            ]) }}""",
        "tab_bar": """
            {% call tab_bar("Example tabs") %}
              {{ tab_item("Overview", active=True, href="#macro-tab_bar") }}
              {{ tab_item("History", href="#macro-tab_bar") }}
            {% endcall %}""",
        "tab_item": """
            {% call tab_bar("Example tabs") %}
              {{ tab_item("Overview", href="#macro-tab_item") }}
              {{ tab_item("History", active=True, href="#macro-tab_item") }}
            {% endcall %}""",
        "chip": """
            <div class="flex gap-1.5">
              {{ chip("1h", href="#macro-chip") }}
              {{ chip("24h", active=True, href="#macro-chip") }}
              {{ chip("7d", href="#macro-chip") }}
            </div>""",
        "copy_button": """{{ copy_button("redis://localhost:6379/0") }}""",
        "brand": """{{ brand("Steward") }}""",
        "nav_item": """
            {% set section = {"key": "overview", "label": "Overview", "path": "/overview",
                              "icon": "M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1v-9.5Z"} %}
            <nav class="w-56">{{ nav_item(section, "/overview") }}</nav>""",
        "appearance_fields": """
            <div x-data="{ ...appearance() }" class="w-64 space-y-2">{{ appearance_fields() }}</div>""",
        "chip_nav": """
            {{ chip_nav("catalog-window", "Window", [("30d", True, "#macro-chip_nav"), ("90d", False, "#macro-chip_nav")], "#catalog-window") }}""",
        "mark": """
            <span class="inline-flex items-center gap-3">
              {{ mark({"icon_url": None, "category": "Groceries"}, "Market") }}
              {{ mark({"icon_url": None, "category": None}, "Rent") }}
            </span>""",
        "file_mark": """{{ file_mark(file_badge("application/pdf", "statement.pdf")) }}""",
        "contact_link": """{{ contact_link(7, "County Clerk") }}""",
        "doors": """
            {{ doors([{"label": "Checking", "url": "/accounts/1"}, {"label": "Taxes", "url": "/matters/2"},
                      {"label": "Renewal", "url": "/matters/3"}], show=2) }}""",
        "dot": """
            <span class="flex items-center gap-3">
              {{ dot("ok", "Synced today") }} {{ dot("warn", "A week old") }} {{ dot("error", "A month old") }}
            </span>""",
        "remove_button": """
            <div class="flex w-48 items-center">Groceries{{ remove_button("/budget/envelopes/1/remove", "Remove Groceries") }}</div>""",
        "item_grid": """
            {% call item_grid("catalog-goals") %}
              {% call item_card("catalog-goal-1") %}<p class="text-sm">Emergency fund</p>{% endcall %}
              {% call item_card("catalog-goal-2") %}<p class="text-sm">New roof</p>{% endcall %}
            {% endcall %}""",
        "item_card": """
            <ul class="grid gap-4 sm:grid-cols-2">
              {% call item_card("catalog-card") %}<p class="text-sm">One card of a grid</p>{% endcall %}
            </ul>""",
        "command": """<p class="flex items-center gap-2">{{ command("make serve") }}</p>""",
        "sparkline": """{{ sparkline("0.0,20.0 30.0,8.0 60.0,14.0 90.0,2.0 120.0,10.0", extra="w-40 h-6") }}""",
        "copy_icon": """<span class="group inline-flex items-center gap-1.5">redis://localhost:6379/0{{ copy_icon("redis://localhost:6379/0", "opacity-0 group-hover:opacity-100") }}</span>""",
        "code_block": """
            {{ code_block("SELECT id, email FROM user LIMIT 10;", caption="users.sql", lang="sql") }}""",
        "highlighted": """
            {{ highlighted("Traceback (most recent call last):\\n  File \\"app.py\\", line 3, in run\\nValueError: bad", "pytb", wrap=True) }}""",
        "facts": """
            {{ facts([("Engine", "taskiq 0.12.6"), ("Processes", 2), ("Queues", ["system", "load_test"])]) }}""",
        "stat_row": """
            {{ stat_row([("Processes", 2), ("Concurrency", 50), ("Timeout", "300s")]) }}""",
    },
    "feedback": {
        "toast_region": """
            {# Mounted once by base.html. A route raises a toast with
               with_toast(response, "Saved"); a page with $dispatch('toast', ...). #}
            {{ toast_region() }}""",
        "empty_state": """
            {{ empty_state("No jobs yet", "Enqueue one from the CLI to see it here.") }}""",
        "error_banner": """
            {{ error_banner(["Email is already registered.", "Password is too short."]) }}""",
        "sse_job": """
            {# Follows /jobs/<id>/events until the job's terminal frame. #}
            {{ sse_job("import-42", "Importing") }}""",
        "notice": """
            {{ notice("catalog-waiting", 3, "/review", "3 transactions are waiting for review") }}""",
        "oob": """
            {# Sent beside a swapped row; htmx puts it wherever #queue-count is. #}
            {% call oob("queue-count") %}1,204{% endcall %}""",
    },
    "form": {
        "primary_button": """
            {% set on_click %}@click="$dispatch('toast', { text: 'Clicked', tone: 'ok' })"{% endset %}
            {{ primary_button("Save", attrs=on_click) }}""",
        "submit_button": """
            <form x-data="{ loading: false }"
                  @submit.prevent="loading = true; setTimeout(() => loading = false, 1500)">
              {{ submit_button("Sign in", "Signing in") }}
            </form>""",
        "select": """
            {{ select("queue", [{"id": "system", "name": "system"}, {"id": "load_test", "name": "load_test"}], label="Queue") }}""",
        "date_input": """{{ date_input("since", "2026-09-01", label="Since") }}""",
        "checkbox": """{{ checkbox("failed_only", True, "Failed only") }}""",
        "search_input": """{{ search_input("catalog", placeholder="Search keys") }}""",
        "filter_input": """{{ filter_input("#catalog-demo-rows", placeholder="Filter rows") }}""",
        "field": """
            {% call field("Email", error="Enter a valid address") %}
              {{ text_input("email", "ops@") }}
            {% endcall %}""",
        "text_input": """{{ text_input("name", placeholder="Display name") }}""",
        "money_input": """{{ money_input("amount", "12.50") }}""",
        "number_input": """{{ number_input("speed", 1.25, step=0.05, min=0.25, max=4) }}""",
        "textarea": """{{ textarea("notes", rows=3, placeholder="Notes") }}""",
        "password_input": """
            {{ password_input("password", placeholder="Password", required=False) }}""",
        "range_chips": """{{ range_chips([(1, "1d"), (7, "7d"), (30, "30d")], 7) }}""",
        "checklist": """{{ checklist("example-services", "service", "Services", [{"value": "redis", "label": "Cache", "checked": True}, {"value": "worker", "label": "Worker", "checked": False, "count": 3}]) }}""",
        "range_form": """{{ range_form([(1, "1d"), (7, "7d"), (30, "30d")], 7, "/overseer", "#overseer-main") }}""",
        "account_filter": """
            {# The accounts' dropdown needs account rows (account_sections); without
               any the filter is its window chips. #}
            {{ account_filter("/overview", [], [], 30, [(30, "30d"), (90, "90d")]) }}""",
        "control": """<input type="text" placeholder="A bare control" class="{{ control('input') }}">""",
        "button_classes": """
            <span class="flex gap-2">
              <button type="button" class="{{ button_classes() }}">Save</button>
              <button type="button" class="{{ button_classes('default') }}">Cancel</button>
            </span>""",
        "check_apply": """<form>{{ check_apply("Merge", True) }}</form>""",
        "summary_panel": """
            {% call summary_panel('data-summary') %}<dt>Rows</dt><dd class="text-right">12</dd>{% endcall %}""",
        "pick_or_add": """
            {{ pick_or_add("payee_id", [{"id": 1, "name": "Market"}, {"id": 2, "name": "Landlord"}], new_field="payee_name") }}""",
        "file_input": """{{ file_input("document", accept=".pdf") }}""",
        "picker": """
            {{ picker("catalog-picker", "/register/1/payee", "payee_id",
                      [{"id": 1, "name": "Market", "fact": "12x"}, {"id": 2, "name": "Landlord"}],
                      hint="Search payees", create="payee_name") }}""",
        "target_inputs": """<form>{{ target_inputs(4, None, None) }}</form>""",
        "money_edit": """<form>{{ money_edit("limit", "400.00", "$400.00", "Edit the limit") }}</form>""",
        "term_fields": """
            {% set terms = [{"name": "rate", "label": "Rate", "kind": "text", "choices": ()},
                            {"name": "opened", "label": "Opened", "kind": "date", "choices": ()}] %}
            {{ term_fields(terms, {"rate": "6.25%"}) }}""",
        "chip_select": """
            {{ chip_select("places", [{"id": 1, "name": "Checking"}, {"id": 2, "name": "Taxes"}], [{"id": 1, "name": "Checking"}]) }}""",
        "line_row": """{{ line_row("label", "value", "Policy", "H-123") }}""",
        "line_rows": """{{ line_rows("label", "value", [("Policy", "H-123")]) }}""",
        "confirm_permanent": """
            {# Below a dialog's archive action; the route checks the typed name. #}
            {{ confirm_permanent("this account", "Checking", "/accounts/1/purge") }}""",
        "action": """
            {% set on_click %}@click="$dispatch('toast', { text: 'Clicked', tone: 'ok' })"{% endset %}
            <div class="flex flex-wrap gap-2">
              {{ action("Retry", on_click, "primary") }}
              {{ action("Pause", "", "warn") }}
              {{ action("Delete", "", "danger") }}
              {{ action("Details", "") }}
            </div>""",
    },
    "table": {
        "data_table": """
            {% set columns = [
              {"key": "queue", "label": "Queue"},
              {"key": "state", "label": "State", "kind": "status"},
              {"key": "waiting", "label": "Waiting", "kind": "int", "align": "right"},
            ] %}
            {{ data_table(columns, [
              {"queue": "system", "state": {"label": "Healthy", "tone": "ok"}, "waiting": 3},
              {"queue": "load_test", "state": {"label": "Backed up", "tone": "warn"}, "waiting": 1204},
            ]) }}""",
        "table_row": """
            <table class="w-full text-sm"><tbody>
              {{ table_row([{"key": "queue", "label": "Queue"}, {"key": "waiting", "label": "Waiting", "kind": "int", "align": "right"}],
                           {"queue": "system", "waiting": 3}) }}
            </tbody></table>""",
        "new_dot": """<span class="inline-flex items-center gap-2 text-sm">{{ new_dot() }} New</span>""",
        "new_mark": """
            <table class="w-full text-sm"><tbody><tr><td class="relative pl-5">{{ new_mark({"is_new": True, "import_batch_id": None}) }}Coffee</td></tr></tbody></table>""",
        "pager": """
            {{ pager({"start": 26, "end": 50, "total": 80, "prev": "?page=1", "next": "?page=3"}, "#jobs") }}""",
    },
}


class MacroEntry(NamedTuple):
    name: str
    signature: str
    doc: str
    example: str
    preview: Markup | None
    error: str | None


def _render(imports: str, example: str) -> tuple[Markup | None, str | None]:
    """An example's HTML, or what went wrong rendering it."""
    try:
        return Markup(templates.env.from_string(imports + example).render()), None
    except Exception as exc:  # noqa: BLE001 - shown on the entry, logged here
        logger.warning("Macro example failed to render", error=str(exc))
        return None, str(exc)


def _source(path: str) -> str:
    source, _, _ = templates.env.loader.get_source(templates.env, path)  # type: ignore[union-attr]
    return source


def catalog(section: str) -> list[MacroEntry]:
    """Every public macro in the section's file, in source order.

    Built once per process until an input changes: the macro files (they
    import each other) and this section's examples are the cache key, so
    an edited macro shows on the next load without a restart.
    """
    sources = tuple(_source(path) for path in FILES.values())
    return _catalog(section, sources, tuple(sorted(EXAMPLES[section].items())))


@lru_cache(maxsize=16)
def _catalog(
    section: str, sources: tuple[str, ...], examples: tuple[tuple[str, str], ...]
) -> list[MacroEntry]:
    path = FILES[section]
    source = sources[list(FILES).index(section)]
    found = [m for m in _MACRO.finditer(source) if not m["name"].startswith("_")]
    imports = f'{{% from "{path}" import {", ".join(m["name"] for m in found)} %}}'
    entries = []
    for match in found:
        name = match["name"]
        example = dedent(dict(examples).get(name, "")).strip()
        preview, error = (
            (None, None) if name in CODE_ONLY else _render(imports, example)
        )
        if not example:
            error = "No example yet"
        doc = " ".join((match["doc"] or "").split())
        entries.append(
            MacroEntry(name, match["signature"], doc, example, preview, error)
        )
    return entries
