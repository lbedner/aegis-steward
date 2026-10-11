"""One GET of a URL somebody else chose, kept on the public internet (#485).

The assistant picks the hosts ``look_up_contact`` reads, and an injected
document can pick them for her. Left alone, httpx would happily read an
internal HTTPS service or the cloud metadata address and hand the text
back to the model.

So the host is resolved HERE, every address it resolves to must be
public, and the request is sent to the checked IP rather than the name:
a second lookup at connect time is exactly where a rebinding name swaps
in 127.0.0.1. The Host header and the TLS server name (``sni_hostname``,
which httpcore also uses to verify the certificate) stay the original
host's, so virtual hosting and certificate checks behave as if the name
had been dialled.

Redirect policy is the caller's: ``guarded_get`` never follows one, and a
caller that does sends the next URL back through here, so the target is
resolved and checked again. The body is the caller's to judge too, and
is never read past ``MAX_BODY_BYTES``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import ipaddress
import socket

import httpx

# The most of a body any caller reads. A contact page is tens of KB; a
# host the model chose can answer with an endless one.
MAX_BODY_BYTES = 2 * 1024 * 1024

# NAT64's well-known prefix carries an IPv4 address in its last 32 bits,
# and a cloud NAT64 gateway will route it to a private one.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class BlockedAddressError(Exception):
    """The host resolves somewhere this app must not fetch from."""


def is_public(address: str) -> bool:
    """Whether an address is on the public internet.

    IPv4 written as IPv6 (``::ffff:127.0.0.1``, NAT64) is judged as the
    IPv4 it carries. ``is_global`` rules out private, loopback,
    link-local, unspecified and shared address space (100.64.0.0/10,
    where one cloud keeps its metadata service); multicast and reserved
    are not covered by it.
    """
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in _NAT64:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip.is_global and not (ip.is_multicast or ip.is_reserved)


async def _resolve(host: str, port: int) -> list[str]:
    """Every address a host resolves to (scope ids dropped)."""
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    )
    return list(dict.fromkeys(str(info[4][0]).split("%")[0] for info in infos))


@asynccontextmanager
async def guarded_get(
    client: httpx.AsyncClient, url: str
) -> AsyncIterator[httpx.Response]:
    """GET ``url`` at a checked public address, body unread, or raise.

    Raises ``BlockedAddressError`` when any address the host resolves to
    is not public; nothing is sent. Never follows a redirect itself. The
    body is streamed: check status and content-type, then ``read_capped``
    it. The response is closed on the way out either way.
    """
    target = httpx.URL(url)
    if target.scheme not in ("http", "https") or not target.host:
        raise BlockedAddressError(f"not a web URL: {url}")
    port = target.port or (443 if target.scheme == "https" else 80)
    # The client's connect timeout covers the lookup too, as it did when
    # httpx resolved the name itself.
    async with asyncio.timeout(client.timeout.connect):
        addresses = await _resolve(target.host, port)
    if not addresses or not all(is_public(address) for address in addresses):
        raise BlockedAddressError(f"{target.host} resolves to {addresses}")
    extensions = {"sni_hostname": target.host} if target.scheme == "https" else {}
    request = client.build_request(
        "GET",
        target.copy_with(host=addresses[0]),
        headers={"Host": target.netloc.decode("ascii")},
        extensions=extensions,
    )
    response = await client.send(request, stream=True)
    try:
        yield response
    finally:
        await response.aclose()


async def read_capped(response: httpx.Response, limit: int = MAX_BODY_BYTES) -> str:
    """The text of a streamed body, read no further than ``limit`` bytes.

    Counted after content-decoding, so a small gzip bomb is cut off too.
    """
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body += chunk
        if len(body) >= limit:
            break
    return bytes(body[:limit]).decode(
        response.charset_encoding or "utf-8", errors="replace"
    )
