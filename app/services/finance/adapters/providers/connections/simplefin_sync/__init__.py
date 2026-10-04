"""SimpleFIN connections: claim a setup token, sync the banks behind it.

A package, like its siblings:

- ``mapping``  SimpleFIN's accounts and transactions in this app's terms
- ``sync``     one pass over a connection, connecting one, and its adapter

Everything lands through the shared upserts the other aggregators use.
Writes but does not commit - the caller owns the transaction.
"""

from app.services.finance.adapters.providers.connections.simplefin_sync.sync import (
    ADAPTER,
    connect_simplefin,
    sync_simplefin_connection,
)

__all__ = ["ADAPTER", "connect_simplefin", "sync_simplefin_connection"]
