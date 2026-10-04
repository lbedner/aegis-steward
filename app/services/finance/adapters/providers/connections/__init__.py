"""Provider connection sync: turn provider links into finance accounts + txns.

One package per aggregator (``plaid_sync``, ``snaptrade_sync``,
``simplefin_sync``) for its own connect/sync path, each mapping its
payload onto the shared shapes in ``upserts`` and declaring a
``ProviderAdapter``; ``registry`` for the verbs that dispatch across them
by that adapter table; ``common`` for the adapter shape, the helpers
every adapter shares and the ``SyncResult`` they all report in.

The per-provider packages never import each other; only ``registry``
lists them. Adding an aggregator means a new sibling package and its
adapter in ``registry.ADAPTERS``, and nothing else in the package moves.

The ``_sync`` suffix is load-bearing: ``providers/plaid.py``,
``providers/snaptrade.py`` and ``providers/simplefin.py`` next door are
the API clients. These modules
are the sync logic that drives them, and the two are easy to confuse from
a filename alone.

Writes but does not commit - the caller owns the transaction.
"""

from app.services.finance.adapters.providers.connections import (
    common,
    plaid_sync,
    registry,
    simplefin_sync,
    snaptrade_sync,
)
from app.services.finance.adapters.providers.connections.common import (
    SyncResult,
    get_connection,
    list_plaid_connections,
    list_provider_connections,
)
from app.services.finance.adapters.providers.connections.plaid_sync import (
    complete_hosted_link,
    create_plaid_connection,
    fire_sandbox_webhook,
    process_plaid_webhook,
    refresh_webhook_urls,
    relink_connection,
    sync_plaid_connection,
)
from app.services.finance.adapters.providers.connections.registry import (
    disconnect_connection,
    sync_one_connection,
    sync_owner_connections,
)
from app.services.finance.adapters.providers.connections.simplefin_sync import (
    connect_simplefin,
    sync_simplefin_connection,
)
from app.services.finance.adapters.providers.connections.snaptrade_sync import (
    complete_snaptrade_connect,
    start_snaptrade_connect,
    sync_snaptrade_connection,
)

__all__ = [
    "SyncResult",
    "common",
    "complete_hosted_link",
    "complete_snaptrade_connect",
    "connect_simplefin",
    "create_plaid_connection",
    "disconnect_connection",
    "fire_sandbox_webhook",
    "get_connection",
    "list_plaid_connections",
    "list_provider_connections",
    "plaid_sync",
    "process_plaid_webhook",
    "refresh_webhook_urls",
    "registry",
    "relink_connection",
    "simplefin_sync",
    "snaptrade_sync",
    "start_snaptrade_connect",
    "sync_one_connection",
    "sync_owner_connections",
    "sync_plaid_connection",
    "sync_simplefin_connection",
    "sync_snaptrade_connection",
]
