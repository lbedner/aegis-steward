"""Tests for the router.

These tests exercise the router's auth gate, redirect-to-login, and
view-lifecycle wiring — all behavior that only matters when
``include_auth=true``. Without auth, the router still ships but its auth
guard is a no-op, so this whole file is gated to keep the generated
project's test suite tidy.
"""
