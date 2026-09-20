"""Business logic does not reach up into infrastructure.

CLAUDE.md puts ``app/components/`` as WHEN and WHERE - the backend, the
frontend, the worker - and ``app/services/`` as WHAT. A service importing
a router or a template filter inverts that, and it always happens the
same way: two callers need one small thing, so it gets left in whichever
caller was written first, and the other one reaches across for it.

The cost is not abstract. ``run_import`` imported
``app.components.backend.api.finance.imports`` for an eleven-line dict
shaper, and importing a router module brings FastAPI with it: measured
in the worker on 2026-09-19, 8 MiB and six backend modules loaded into a
process that serves no HTTP. ``writes/terms.py`` was worse - a
module-level import of the web frontend's filters, so the finance
service pulled the frontend in eagerly, for one pure function.

Each of the five was fixed by putting the shared thing in the layer that
owns it, which is where both callers could already see it.
"""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "app" / "services"

# The layers a service must not import. ``app.core`` is shared ground and
# stays allowed; these three are the ones that own a delivery mechanism.
FORBIDDEN = (
    "app.components.backend",
    "app.components.frontend",
    "app.components.web_frontend",
)


def _imported_modules(source: str) -> list[str]:
    """Every module named by an import, module-level or inside a function.

    A deferred import is still a dependency - it just fails later, in a
    worker, on a file. The AST sees both.
    """
    names: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.append(node.module)
    return names


def test_no_service_imports_a_delivery_layer() -> None:
    offenders: list[str] = []
    for path in sorted(SERVICES.rglob("*.py")):
        for module in _imported_modules(path.read_text()):
            if module.startswith(FORBIDDEN):
                offenders.append(f"{path.relative_to(ROOT)} -> {module}")
    assert not offenders, "services reaching into components:\n  " + "\n  ".join(
        offenders
    )


def test_shaping_an_import_result_needs_no_web_framework() -> None:
    """The worker's half of the contract, proved in a fresh process.

    A subprocess because the point is what gets IMPORTED, and pytest has
    already loaded half the app by the time this runs.
    """
    probe = (
        "import sys\n"
        "from app.services.finance.schemas.imports import import_result_payload\n"
        "assert 'fastapi' not in sys.modules, 'pulled in FastAPI'\n"
        "backend = [m for m in sys.modules if m.startswith('app.components.backend')]\n"
        "assert not backend, backend\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr[-1500:]
