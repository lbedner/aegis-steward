"""Every port the stack publishes is on the host's loopback only.

A bare ``"8001:8000"`` binds 0.0.0.0: on any Wi-Fi the laptop joined,
anyone could open the app (it has no login) or write to Redis, whose
cache and job payloads are pickled, which is code execution. Docker's
published ports also skip the host firewall. Containers still reach each
other by service name; only the host side is narrowed.

The one exception is the server's front door: in prod Traefik takes 80 and
443 on every interface, and nothing else does.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ("docker-compose.yml", "docker-compose.dev.yml", "docker-compose.prod.yml")
PUBLIC = {("docker-compose.prod.yml", "traefik"): {"80:80", "443:443"}}


def test_every_published_port_binds_loopback() -> None:
    published = []
    for name in COMPOSE:
        compose = yaml.safe_load((ROOT / name).read_text()) or {}
        for service, spec in (compose.get("services") or {}).items():
            for port in (spec or {}).get("ports") or []:
                published.append((name, service, str(port)))

    assert published, "the stack publishes its webserver"
    for name, service, port in published:
        if (name, service) in PUBLIC:
            assert port in PUBLIC[name, service], f"{name}: {service} publishes {port}"
            continue
        assert port.startswith("127.0.0.1:"), f"{name}: {service} publishes {port}"
