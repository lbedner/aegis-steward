"""The worker that imports a ledger needs more than maintenance-task room.

``worker-system`` was given 256M and 0.25 CPU as the "maintenance and
administrative tasks" worker, then had the two heaviest jobs in the app
put on it: ``finance_import_task`` and ``extract_document_task``. An
18,618-row import (2026-09-19) was SIGKILLed twelve seconds in - the
container idles near 110 MiB before the finance service is even imported,
and the plan holds every existing transaction plus every planned row at
once. arq retried it five times, each one killed the same way, which is
what the spinner in the browser was waiting on.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

# The import peaks well above the idle footprint; this is the headroom the
# 18,618-row file needs, not a measured ceiling.
MINIMUM_WORKER_MEMORY_MB = 1024


def _limits(service: str) -> dict[str, str]:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text()) or {}
    spec = (compose.get("services") or {})[service]
    return spec["deploy"]["resources"]["limits"]


def _megabytes(value: str) -> int:
    text = str(value).strip().upper().removesuffix("B")
    return int(text[:-1]) * 1024 if text.endswith("G") else int(text[:-1])


def test_the_system_worker_can_hold_a_ledger_import() -> None:
    assert _megabytes(_limits("worker-system")["memory"]) >= MINIMUM_WORKER_MEMORY_MB


def test_the_system_worker_gets_a_whole_cpu() -> None:
    # 0.25 is why the import was slow as well as dead: every one of the
    # 18,618 rows is planned and written by this process.
    assert float(_limits("worker-system")["cpus"]) >= 1.0
