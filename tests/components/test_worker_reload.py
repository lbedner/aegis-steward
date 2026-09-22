"""The worker's dev reload restarts the process, not the connection.

``arq --watch`` looks like a reloader and is not one: on a file change
it closes the worker and calls ``async_run()`` again in the SAME
process, with the same already-imported modules. It prints "files
changed, reloading arq worker..." and runs the old code. A mail import
filed two logos as attachments an hour after the rule against it
landed, twice, before anyone believed the message (2026-09-21).

The scheduler branch beside it had this right: ``watchfiles`` running
the entrypoint as a child process, restarted on change.
"""

from pathlib import Path
import re

ENTRYPOINT = Path(__file__).resolve().parents[2] / "scripts" / "entrypoint.sh"


def test_the_worker_is_restarted_by_watchfiles_not_reconnected_by_arq() -> None:
    source = ENTRYPOINT.read_text()
    worker = source[source.index('"$run_command" = "worker"') :]
    worker = worker[: worker.index("elif")]

    assert "--watch" not in worker, "arq's watch flag reconnects; it never re-imports"
    assert re.search(r"watchfiles --filter python", worker), (
        "the dev branch must run the worker under watchfiles, as the scheduler does"
    )
