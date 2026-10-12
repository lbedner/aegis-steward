"""What the server runs: ``docker-compose.prod.yml`` over ``docker-compose.yml``,
given ``.env.deploy`` (whose committed template is ``.env.deploy.example``).
``aegis deploy`` copies that file to the server as ``.env``, so compose fills
the ``${...}`` in both compose files from it and every container loads it."""

import json
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
import yaml

from app.core.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def _compose(name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / name).read_text())


def _labels(service: dict[str, Any]) -> dict[str, str]:
    return dict(label.split("=", 1) for label in service.get("labels") or [])


def test_the_load_test_worker_stays_off_the_server() -> None:
    profiles = _compose("docker-compose.yml")["services"]["worker-load-test"][
        "profiles"
    ]
    assert "prod" not in profiles


def test_traefik_stays_off_the_laptop() -> None:
    """Dev reaches the webserver on its own port. Another stack's Traefik
    already holds 80 and 8080 on the laptop, so a dev Traefik would fail
    ``make serve``, and nothing in dev needs the Docker socket."""
    services = _compose("docker-compose.yml")["services"]
    for name in ("traefik", "socket-proxy"):
        assert services[name]["profiles"] == ["prod"], name


def test_traefik_refuses_an_upload_over_the_apps_largest_cap() -> None:
    """A 413 at the proxy before the app reads a byte, a little over the cap
    so an allowed upload's multipart framing still fits. Only uploads go
    through it: the buffering middleware holds the whole response as well,
    which would stall every streamed one (chat, the live pages)."""
    labels = _labels(_compose("docker-compose.prod.yml")["services"]["webserver"])
    router = "traefik.http.routers.webserver-upload"
    assert "multipart/" in labels[f"{router}.rule"]
    middleware = labels[f"{router}.middlewares"]
    limit = int(
        labels[f"traefik.http.middlewares.{middleware}.buffering.maxRequestBodyBytes"]
    )
    cap = Settings.model_fields["MAIL_IMPORT_MAX_BYTES"].default
    assert cap < limit <= cap * 1.1
    buffered = [
        key
        for key, value in labels.items()
        if key.endswith(".middlewares") and middleware in value.split(",")
    ]
    assert buffered == [f"{router}.middlewares"]


def test_every_router_answers_only_the_domain_over_tls() -> None:
    labels = _labels(_compose("docker-compose.prod.yml")["services"]["webserver"])
    routers = {key.split(".")[3] for key in labels if ".routers." in key}
    assert routers == {"webserver", "webserver-upload", "webserver-admin"}
    for router in routers:
        prefix = f"traefik.http.routers.{router}"
        assert labels[f"{prefix}.rule"].startswith("Host(`${DOMAIN"), router
        assert labels[f"{prefix}.entrypoints"] == "websecure", router
        assert labels[f"{prefix}.tls.certresolver"] == "letsencrypt", router


def test_the_lets_encrypt_address_comes_from_the_env_not_the_repo() -> None:
    """The repo is public: Traefik's static file cannot read the environment,
    so the address is written into it when the container starts."""
    assert "email: __ACME_EMAIL__" in (ROOT / "traefik/traefik.yml").read_text()
    traefik = _compose("docker-compose.prod.yml")["services"]["traefik"]
    assert any(
        e.startswith("ACME_EMAIL=${ACME_EMAIL:?") for e in traefik["environment"]
    )
    assert "__ACME_EMAIL__" in " ".join(traefik["entrypoint"])


def test_redis_needs_its_password_on_the_server() -> None:
    """Dev Redis stays open on loopback; the server's refuses to start
    without ``REDIS_PASSWORD``, and its health check signs in with it."""
    redis = _compose("docker-compose.yml")["services"]["redis"]
    assert "--requirepass" in redis["command"]
    prod = _compose("docker-compose.prod.yml")["services"]["redis"]
    assert "REDISCLI_AUTH=${REDIS_PASSWORD:?" in " ".join(prod["environment"])


def test_the_deploy_env_template_is_a_server_behind_traefik() -> None:
    env = dotenv_values(ROOT / ".env.deploy.example")
    assert (env["APP_ENV"], env["AUTO_RELOAD"], env["TRUST_PROXY_HEADERS"]) == (
        "prod",
        "false",
        "true",
    )
    assert env["AI_PROVIDER"] == "openai"
    assert env["PUBLIC_BASE_URL"] == f"https://{env['DOMAIN']}"
    # localhost: the container's own health check asks for it.
    assert json.loads(env["ALLOWED_HOSTS"]) == [env["DOMAIN"], "localhost"]
    assert env["REDIS_URL"] == f"redis://:{env['REDIS_PASSWORD']}@redis:6379"
    (network,) = _compose("docker-compose.prod.yml")["networks"]["default"]["ipam"][
        "config"
    ]
    assert json.loads(env["TRUSTED_PROXIES"]) == [network["subnet"]]
    # The dev tunnel and its guard exist only where this is set.
    assert "PLAID_TUNNEL_METRICS_URL" not in env
