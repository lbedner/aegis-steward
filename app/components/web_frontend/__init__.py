"""Server-rendered web frontend.

Serves pages at ``/`` from the same webserver that hosts the API and the
Flet dashboard at ``/dashboard``. Jinja2 renders the HTML; htmx and Alpine
drive interactivity from the markup itself, so there is no JavaScript build
chain to run before a page loads.
"""
