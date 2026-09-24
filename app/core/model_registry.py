"""Import every module that defines a table, so ``SQLModel.metadata`` is complete.

Anything that reasons about the whole schema - alembic's ``env.py``,
``migrate-fix``, the startup re-adoption check, the test suite's
``create_all`` - needs every table registered first. Registration is a side
effect of importing the module that defines the class, so this walks the two
places tables live and imports them:

- ``app/models/`` (core tables: users, orgs, conversations)
- ``app/services/<service>/models`` - a package or a single module

A plugin or a hand-written service that keeps its tables there is picked up
without touching anything else. A table defined anywhere else is invisible,
and the generated test ``tests/test_model_registry.py`` fails the build.
"""

from __future__ import annotations

import importlib
import importlib.util
import pkgutil
from types import ModuleType


def _import_tree(module: ModuleType) -> list[str]:
    """Import ``module`` and, if it is a package, every module beneath it."""
    names = [module.__name__]
    if hasattr(module, "__path__"):
        for info in pkgutil.walk_packages(module.__path__, f"{module.__name__}."):
            importlib.import_module(info.name)
            names.append(info.name)
    return names


def import_all_models() -> list[str]:
    """Import every table-defining module. Returns the module names, in order."""
    import app.models as core_models
    import app.services as services

    imported = _import_tree(core_models)
    for service in pkgutil.iter_modules(services.__path__):
        # Only packages can hold a ``models`` submodule; a plain module under
        # app/services (load_test_models.py, say) is not a service.
        if not service.ispkg:
            continue
        name = f"app.services.{service.name}.models"
        try:
            found = importlib.util.find_spec(name) is not None
        except ModuleNotFoundError:
            found = False
        if found:
            imported.extend(_import_tree(importlib.import_module(name)))
    return imported
