"""A fetch of a host somebody else chose, refused off the public internet (#485).

The assistant picks the host ``look_up_contact`` reads, and an injected
document can pick it for her. So the name is resolved here, every address
it resolves to has to be public, and the connection goes to the address
that was checked: a second lookup at connect time could rebind the name
onto an internal service. DNS and the web are faked (``tests/_fake_dns``).
"""

from __future__ import annotations

import httpx
import pytest

from app.core import safe_fetch
from tests._fake_dns import answering, resolving_to


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.10",
        "169.254.169.254",  # the cloud metadata service
        "100.100.100.200",  # shared address space, another metadata host
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fd00:ec2::254",  # the cloud metadata service over IPv6
        "::ffff:127.0.0.1",  # IPv4 written as IPv6
        "64:ff9b::a00:1",  # 10.0.0.1 through a NAT64 gateway
    ],
)
async def test_a_host_resolving_inward_is_refused_before_connecting(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    resolving_to(monkeypatch, [address])
    sent = answering(monkeypatch, httpx.Response(200, text="ok"))

    async with httpx.AsyncClient() as client:
        with pytest.raises(safe_fetch.BlockedAddressError):
            async with safe_fetch.guarded_get(
                client, "https://innocent.example/contact"
            ):
                pass

    assert sent == []


async def test_one_inward_address_among_public_ones_refuses_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A name with a public and a private answer is not a public name."""
    resolving_to(monkeypatch, ["93.184.216.34", "10.0.0.5"])
    sent = answering(monkeypatch, httpx.Response(200, text="ok"))

    async with httpx.AsyncClient() as client:
        with pytest.raises(safe_fetch.BlockedAddressError):
            async with safe_fetch.guarded_get(client, "https://innocent.example/"):
                pass

    assert sent == []


async def test_a_public_host_is_fetched_at_the_address_that_was_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connected by IP, so DNS is not asked twice; the Host header and the
    TLS name stay the host's, so virtual hosting and certificate checks
    work as if the name had been dialled."""
    asked = resolving_to(monkeypatch, ["93.184.216.34"])
    sent = answering(monkeypatch, httpx.Response(200, text="ok"))

    async with httpx.AsyncClient() as client:
        async with safe_fetch.guarded_get(
            client, "https://innocent.example/contact?x=1"
        ) as response:
            assert await safe_fetch.read_capped(response) == "ok"

    assert asked == ["innocent.example"]
    (request,) = sent
    assert request.url.host == "93.184.216.34"
    assert request.url.path == "/contact"
    assert request.url.query == b"x=1"
    assert request.headers["host"] == "innocent.example"
    assert request.extensions["sni_hostname"] == "innocent.example"


async def test_a_public_ipv6_host_keeps_its_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolving_to(monkeypatch, ["2606:4700::1111"])
    sent = answering(monkeypatch, httpx.Response(200, text="ok"))

    async with httpx.AsyncClient() as client:
        async with safe_fetch.guarded_get(client, "https://innocent.example:8443/"):
            pass

    (request,) = sent
    assert request.url.host == "2606:4700::1111"
    assert request.url.port == 8443
    assert request.headers["host"] == "innocent.example:8443"


def test_public_addresses_are_public() -> None:
    assert safe_fetch.is_public("93.184.216.34")
    assert safe_fetch.is_public("2606:4700::1111")
    assert safe_fetch.is_public("::ffff:93.184.216.34")
