"""FastAPI dependencies for the backend API.

Thin re-export shim. The actual definitions live next to the things
they depend on:

* ``get_db`` / ``get_async_db`` — ``app.core.db`` (next to the engine
  factories and ``SessionLocal`` / ``AsyncSessionLocal``).
* ``get_audit`` — ``app.core.audit`` (next to the singleton
  ``audit_emitter``).
* Per-service ``get_<svc>_service`` providers — ``app.services.<svc>.deps``
  (each service owns its own deps file).

Re-exporting here keeps ``from app.components.backend.api.deps import X``
working for any code that still uses the api-namespace import path.
In-tree code now imports directly from the source modules; this shim
exists for plugin authors and any external code that prefers the
single canonical "all deps" import surface.
"""

from app.core.audit import get_audit
from app.core.db import get_async_db, get_db
