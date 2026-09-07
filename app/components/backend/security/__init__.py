"""Backend security primitives.

Implementations of cross-cutting security concerns that are consumed
elsewhere as FastAPI dependencies. The thin ``Depends``-able wrappers
are exposed from ``app.components.backend.api.deps`` so route handlers
can stay ignorant of the implementation module.
"""
