"""Each container is sized for the work it does, not for its name.

Twice now a limit came from what a service was CALLED rather than what
runs in it, and both times the kernel enforced the mistake.

``worker-system`` was given 256M and 0.25 CPU as the "maintenance and
administrative tasks" worker, then had the two heaviest jobs in the app
put on it: ``finance_import_task`` and ``extract_document_task``. An
18,618-row import (2026-09-19) was SIGKILLed twelve seconds in - the
container idles near 110 MiB before the finance service is even imported,
and the plan holds every existing transaction plus every planned row at
once. arq retried it five times, each one killed the same way, which is
what the spinner in the browser was waiting on.

The second is CPU, and it is still costing something now. ``cpus`` is a
ceiling the kernel enforces by FREEZING the process when it is reached -
0.5 means 0.05s of CPU per 0.1s, then a full stop until the next period.
The webserver had 0.5: it serves every page, renders every htmx
fragment, runs Flet's Overseer and streams Illiana's replies, all on one
async event loop, so a freeze stops all of it at once. Read off
``/sys/fs/cgroup/cpu.stat`` on 2026-09-20:

    webserver         12,803 periods   1,515 throttled (11.8%)   83.3s
    scheduler          4,164 periods     231 throttled  (5.5%)   17.9s
    worker-system      8,976 periods       6 throttled  (0.1%)   0.007s

worker-system is the control: same kernel, same accounting, a limit that
fits, six stalls. Meanwhile ``worker-load-test`` - idle unless somebody
is running a load test - held a whole core, more than the process
serving the UI.

The host has 16 cores and the stack asked for three of them, so none of
this was scarcity. A ceiling costs nothing until it is hit; it only ever
decides whether the process gets stopped.
"""

from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[1]

# The import peaks well above the idle footprint; this is the headroom the
# 18,618-row file needs, not a measured ceiling.
MINIMUM_WORKER_MEMORY_MB = 1024


def _limits(service: str) -> dict[str, str]:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text()) or {}
    spec = (compose.get("services") or {})[service]
    limits = spec["deploy"]["resources"]["limits"]
    # Check Compose defaults: overrides can vary by deployment environment.
    return {
        name: match.group(1)
        if (match := re.fullmatch(r"\$\{[^:}]+:-([^}]+)\}", str(value)))
        else str(value)
        for name, value in limits.items()
    }


def _megabytes(value: str) -> int:
    text = str(value).strip().upper().removesuffix("B")
    return int(text[:-1]) * 1024 if text.endswith("G") else int(text[:-1])


def test_the_system_worker_can_hold_a_ledger_import() -> None:
    assert _megabytes(_limits("worker-system")["memory"]) >= MINIMUM_WORKER_MEMORY_MB


def test_the_system_worker_gets_a_whole_cpu() -> None:
    # 0.25 is why the import was slow as well as dead: every one of the
    # 18,618 rows is planned and written by this process.
    assert float(_limits("worker-system")["cpus"]) >= 1.0


# The webserver is the only interactive process in the stack: one async
# loop behind every page, fragment, Flet update and streamed token.
MINIMUM_WEBSERVER_CPUS = 2.0


def test_the_interactive_process_is_not_the_most_constrained() -> None:
    webserver = float(_limits("webserver")["cpus"])
    assert webserver >= MINIMUM_WEBSERVER_CPUS, webserver


def test_no_idle_container_outranks_the_one_serving_the_ui() -> None:
    """``worker-load-test`` had a full core while the webserver had half.

    Not a rule about load testing - a rule about ordering. Whatever a
    background container is given, the process a person is waiting on
    gets at least as much.
    """
    webserver = float(_limits("webserver")["cpus"])
    for service in ("worker-load-test", "scheduler"):
        assert float(_limits(service)["cpus"]) <= webserver, service


def test_the_scheduler_is_off_the_quarter_core_that_throttled_it() -> None:
    # 231 throttles in 4,164 periods, running periodic jobs that touch
    # the same database and services the rest of the stack does.
    assert float(_limits("scheduler")["cpus"]) > 0.25
