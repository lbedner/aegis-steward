"""``finance agents``: the finance agents' prompts, kept in step with code.

Split from ``finance.py`` at the module-size budget. The seam is
honest: these commands are about the agent rows, not the ledger.
"""

from __future__ import annotations

import typer

from app.cli import theme

agents_app = typer.Typer(
    help="The finance agents: prompts in code, rows on this install"
)
console = theme.console()


@agents_app.command("resync-prompts")
def resync_agent_prompts(
    force: bool = typer.Option(
        False, "--force", help="Overwrite a prompt somebody rewrote in the dashboard."
    ),
) -> None:
    """Push the system prompts in CODE onto this install's agent rows.

    The seeder never touches an agent that already exists, so a prompt
    improved in code reaches nobody - silently. Run this after changing
    one. It overwrites the prompt and NOTHING else: a model, a
    temperature or a tool set picked in the dashboard is a choice about
    this install; the prompt is the app's own instructions.
    """
    from app.core.db import db_session
    from app.services.finance.domains.detection.analyst.seeds import (
        resync_finance_agent_prompts,
    )

    with db_session() as session:
        result = resync_finance_agent_prompts(session, force=force)
    if not result:
        console.print("[yellow]No finance agents on this install.[/]")
        return
    for slug, state in sorted(result.items()):
        tone = {"updated": "green", "edited by hand": "yellow"}.get(state, "dim")
        console.print(f"[{tone}]{slug}: {state}[/]")
    if any(state == "edited by hand" for state in result.values()):
        console.print(
            "[yellow]A prompt above was rewritten in the dashboard; "
            "--force overwrites it with the one in code.[/]"
        )
