"""htmx fragment routes.

Partials return HTML fragments rather than whole pages: a route here backs
an ``hx-get``/``hx-post`` on some element and its response is swapped into
the DOM. Keep them beside the page that uses them, mounted under a
``/partials/...`` prefix by ``create_web_frontend_app()``.
"""
