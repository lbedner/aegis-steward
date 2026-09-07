"""Vendor-specific implementations of the ops protocols.

Each adapter is one file, ~50-150 lines, implementing one protocol
against one vendor's API. Today: ``resend`` (mail provider) and
``porkbun`` (registrar). Future adapters slot in alongside without
disturbing existing ones — that's the whole point of the protocol
boundary.
"""
