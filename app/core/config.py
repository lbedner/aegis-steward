# app/core/config.py
"""
Application configuration management using Pydantic's BaseSettings.

This module centralizes application settings, allowing them to be loaded
from environment variables for easy configuration in different environments.
"""

from typing import Any

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Default placeholder bundled with the template — anyone reading the
# template source knows this value, so leaving it unchanged in a non-dev
# deploy would let an attacker forge JWTs. Pinned as a module constant
# so the startup guard below has a single source of truth and a renamed
# placeholder doesn't accidentally bypass the check.
_SECRET_KEY_PLACEHOLDER = "change-this-secret-key-in-production-use-env-variable"


class Settings(
    BaseSettings,
):
    """
    Defines application settings.
    `model_config` is used to specify that settings should be loaded from a .env file.
    """

    # Project identity
    PROJECT_NAME: str = "aegis-steward"
    # Friendly display name shown to humans (email FROM/body, browser
    # titles, etc.). PROJECT_NAME above is the URL-safe slug; this is
    # the marketing-friendly form. Defaults to the value you typed when
    # generating the project; override per-environment via env var.
    PROJECT_DISPLAY_NAME: str = "aegis-steward"

    # Public-facing base URL (used to build absolute links in outgoing
    # emails - receipts, password reset, subscription welcome, etc.).
    # Override per environment via .env (e.g. https://app.your-domain.com);
    # the localhost default keeps dev working without configuration.
    PUBLIC_BASE_URL: str = "http://localhost:8000"

    # Application environment: "dev" or "prod"
    APP_ENV: str = "dev"

    # API docs auth (HTTP Basic). When both are set, /docs /redoc /openapi.json
    # require these creds. When unset: open in dev, 404 in any other APP_ENV.
    DOCS_USERNAME: str = ""
    DOCS_PASSWORD: str = ""

    # Public signup gate. When False, /register returns 403 and OAuth new-user
    # creation is refused. Existing users still sign in normally.
    REGISTRATION_ENABLED: bool = True

    # Log level for the application
    LOG_LEVEL: str = "INFO"

    # Port for the web server
    PORT: int = 8000

    # Development settings
    AUTO_RELOAD: bool = False

    # Docker settings (used by docker-compose)
    AEGIS_STACK_TAG: str = "aegis-stack:latest"
    AEGIS_STACK_VERSION: str = "dev"

    # Health monitoring and alerting
    # Health checks are available via API endpoints (/health/)
    # Use external monitoring tools (Prometheus, DataDog, etc.) to poll these endpoints
    HEALTH_CHECK_ENABLED: bool = True
    HEALTH_CHECK_INTERVAL_MINUTES: int = 5  # Recommended interval for monitoring

    # Health check performance settings
    HEALTH_CHECK_TIMEOUT_SECONDS: float = 2.0
    SYSTEM_METRICS_CACHE_SECONDS: int = 5

    # Basic alerting configuration
    ALERTING_ENABLED: bool = False
    ALERT_COOLDOWN_MINUTES: int = 60  # Minutes between repeated alerts for same issue

    # Health check thresholds
    MEMORY_THRESHOLD_PERCENT: float = 90.0
    DISK_THRESHOLD_PERCENT: float = 85.0
    CPU_THRESHOLD_PERCENT: float = 95.0

    # Where stored objects live: chat attachments, documents, anything
    # addressed by content hash. A directory today; the storage component
    # points this at a bucket without any caller noticing.
    STORAGE_ROOT: str = "storage_data"
    # Which backend get_storage() builds: the storage component flips this
    # to a bucket; everything else keeps the directory above.
    STORAGE_BACKEND: str = "filesystem"

    # Flet frontend settings
    FLET_ASSETS_DIR: str = "assets"  # Directory for Flet static assets (images, etc.)

    # Web frontend settings (server-rendered pages at /). Both paths are
    # relative to the project root; the static dir holds the sources, and a
    # build writes fingerprinted output into its ``dist/`` subdirectory.
    WEB_TEMPLATES_DIR: str = "app/components/web_frontend/templates"
    WEB_STATIC_DIR: str = "app/components/web_frontend/static"
    # One-liner describing the project. Rendered in the landing hero and
    # in its SEO meta description; defaults to what you typed when
    # generating the project.
    PROJECT_DESCRIPTION: str = (
        "A production-ready async Python application built with Aegis Stack"
    )

    # Authentication settings
    AUTH_ENABLED: bool = False  # Auth service not included; dev user is synthesized
    DEV_USER_ROLE: str = "admin"  # Role for synthetic dev user when AUTH_ENABLED=false
    # Email allowlist for ``require_admin`` — gates operator-only routes
    # (refund actions, dispute review, aggregate revenue charts, etc.).
    # Empty list 403s everyone when ``AUTH_ENABLED=True``; safe default
    # before someone deliberately grants themselves admin. Set in
    # ``.env`` as a JSON list: ``ADMIN_USER_EMAILS=["you@example.com"]``.
    ADMIN_USER_EMAILS: list[str] = []
    INVITE_ACCEPTANCE_MODE: str = (
        "email"  # "email" requires email match, "token" allows anyone with token
    )
    SECRET_KEY: str = _SECRET_KEY_PLACEHOLDER
    # Distinct secret for column-level at-rest encryption (AES-GCM v2 in
    # ``app/core/encryption.py``). Splits JWT-signing and credential-
    # encryption blast radii so leaking one secret doesn't compromise
    # both. When unset, encryption.py falls back to ``SECRET_KEY`` with
    # a one-time warning. Set in ``.env`` for production deploys.
    ENCRYPTION_KEY: str | None = None
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    # Dev-only multiplier on ``ACCESS_TOKEN_EXPIRE_MINUTES``. When
    # ``APP_ENV == "dev"``, the effective session length becomes
    # ``ACCESS_TOKEN_EXPIRE_MINUTES * DEV_TOKEN_EXPIRE_MULTIPLIER`` so
    # devs aren't logged out every 15 minutes during iteration. Prod /
    # staging are unaffected (the multiplier doesn't fire). Override in
    # ``.env`` if 4x isn't enough — e.g. ``DEV_TOKEN_EXPIRE_MULTIPLIER=16``.
    DEV_TOKEN_EXPIRE_MULTIPLIER: int = 4
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14
    PASSWORD_RESET_EXPIRE_MINUTES: int = 60
    EMAIL_VERIFICATION_EXPIRE_HOURS: int = 24

    # Rate limiting settings
    RATE_LIMIT_LOGIN_MAX: int = 5
    RATE_LIMIT_LOGIN_WINDOW: int = 60
    RATE_LIMIT_REGISTER_MAX: int = 3
    RATE_LIMIT_REGISTER_WINDOW: int = 60
    # Resend-verification is a user-initiated button; a tighter-than-register
    # limit would feel punishing during legit retries. 3 per 5 min strikes
    # the balance — still spam-proof, far less fiddly.
    RATE_LIMIT_RESEND_VERIFICATION_MAX: int = 3
    RATE_LIMIT_RESEND_VERIFICATION_WINDOW: int = 300

    # Account lockout settings
    ACCOUNT_LOCKOUT_ATTEMPTS: int = 5
    ACCOUNT_LOCKOUT_MINUTES: int = 15

    # Proxy trust settings
    TRUST_PROXY_HEADERS: bool = False  # Set to True only behind a trusted reverse proxy

    # Traffic monitor: per-source request-volume visibility ("who's hammering
    # you") rendered in Overseer. Counts requests per client IP, bucketed
    # hourly; backed by Redis when present (shared across processes), in-memory
    # otherwise (per-process, resets on restart).
    TRAFFIC_MONITOR_ENABLED: bool = True
    # Trailing window the Overseer panel ranks sources over (hours).
    TRAFFIC_WINDOW_HOURS: int = 24
    # Read-time dominance flag: a single source over this share of windowed
    # traffic (and clearing the absolute floor) is highlighted as "hammering."
    TRAFFIC_DOMINANCE_SHARE: float = 0.5
    TRAFFIC_DOMINANCE_FLOOR: int = 100

    # HttpOnly session cookie holding the JWT after login / register /
    # OAuth callback. None = auto (off in dev, on otherwise). Set to
    # False to run prod over HTTP without TLS — browsers refuse Secure
    # cookies on insecure origins. Read by ``set_session_cookie`` in
    # ``app.core.security``, which fires for every browser-side auth
    # surface (not just OAuth), so this setting must exist whenever
    # ``include_auth`` is true.
    SESSION_COOKIE_SECURE: bool | None = None

    # Redis settings for arq background tasks
    REDIS_URL: str = "redis://redis:6379"  # Docker service name by default
    REDIS_URL_LOCAL: str | None = None  # Manual override for local CLI usage
    # Host publish port chosen by `make serve` (loaded from .env.ports).
    # None = use the container port. See `_localhost_url`.
    REDIS_HOST_PORT: int | None = None
    REDIS_DB: int = 0
    # Logical Redis DB for the application cache (view caches, bulk
    # caches, etc.). Separate from REDIS_DB so ``cache.clear()`` can't
    # accidentally nuke arq's queue (which lives on REDIS_DB) and so a
    # FLUSHDB against either namespace doesn't take the other down.
    CACHE_REDIS_DB: int = 1

    @property
    def redis_url_effective(self) -> str:
        """Get effective Redis URL, preferring local override when not in Docker."""
        if self.REDIS_URL_LOCAL and not self.is_docker:
            return self.REDIS_URL_LOCAL
        if not self.is_docker:
            return self._localhost_url(self.REDIS_URL, {"redis"}, self.REDIS_HOST_PORT)
        return self.REDIS_URL

    # arq worker settings (shared across all workers)
    WORKER_KEEP_RESULT_SECONDS: int = 3600  # Keep job results for 1 hour
    WORKER_MAX_TRIES: int = 3

    # Redis connection settings for arq workers
    REDIS_CONN_TIMEOUT: int = 5  # Connection timeout in seconds (default: 1)
    REDIS_CONN_RETRIES: int = 5  # Connection retry attempts (default: 5)
    REDIS_CONN_RETRY_DELAY: int = 1  # Delay between retries (default: 1)

    # Worker health check settings
    WORKER_HEALTH_CHECK_INTERVAL: int = 15  # In seconds (default: 15)

    # Task history retention (Redis Hashes auto-expire after this)
    TASK_HISTORY_TTL_SECONDS: int = 86400  # 24 hours

    # PURE ARQ IMPLEMENTATION - NO CONFIGURATION NEEDED!
    # Worker configuration comes from individual WorkerSettings classes
    # in app/components/worker/queues/ - just import and use as arq intended!

    # Database settings

    DATABASE_URL: str = "sqlite:///./data/app.db"
    DATABASE_ENGINE_ECHO: bool = False
    DATABASE_CONNECT_ARGS: dict[str, Any] = {"check_same_thread": False}

    # AI Service Configuration
    # Primary service settings
    AI_ENABLED: bool = True
    AI_PROVIDER: str = "public"  # Default to public provider
    AI_MODEL: str = "auto"  # Default model (public provider uses available models)
    AI_TEMPERATURE: float = 0.7
    AI_MAX_TOKENS: int = 1000
    # Anthropic-only thinking depth (low|medium|high|xhigh|max). None = the API
    # default; other providers ignore it. Most of a call's cost is thinking
    # tokens, so "low" cuts spend several-fold on constrained tasks.
    AI_EFFORT: str | None = None
    AI_TIMEOUT_SECONDS: float = 120.0

    # Batch sentiment scoring of conversations. OFF by default: the job
    # spends model tokens on every unscored conversation.
    AI_SENTIMENT_ENABLED: bool = False
    AI_SENTIMENT_BATCH_LIMIT: int = 20

    # Provider API Keys (optional - many providers offer free tiers)
    # Optional for the public provider (LLM7.io): keyless requests use
    # the free anonymous tier (open-weight models); an account key from
    # https://dash.llm7.io unlocks premium models and higher limits.
    LLM7_API_KEY: str | None = None
    # Optional for the pollinations provider: keyless requests use the
    # free anonymous tier (open-weight models, no streaming); a key
    # selects an account tier.
    POLLINATIONS_API_KEY: str | None = None
    OPENAI_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    GOOGLE_API_KEY: str | None = None
    GROQ_API_KEY: str | None = None
    MISTRAL_API_KEY: str | None = None
    COHERE_API_KEY: str | None = None

    # Ollama settings (local LLM inference)

    OLLAMA_BASE_URL: str = "http://host.docker.internal:11434"  # Host mode default

    OLLAMA_BASE_URL_LOCAL: str | None = None  # Manual override for local CLI usage
    # Host publish port chosen by `make serve` (loaded from .env.ports).
    # None = use the container port. See `_localhost_url`.
    OLLAMA_HOST_PORT: int | None = None
    OLLAMA_API_KEY: str | None = None  # Optional, usually not needed

    # Conversation settings
    AI_MAX_CONVERSATION_LENGTH: int = 50  # Max messages per conversation
    AI_CONVERSATION_TIMEOUT_HOURS: int = 24  # Auto-cleanup old conversations

    # Operations / registrar configuration (always present — the `dns`
    # CLI and the ops/ adapters ride alongside any component selection).
    #
    # Porkbun registrar API keys — used by the `dns` CLI and by
    # ``app/services/ops/`` to read and write DNS records. Grab them
    # at porkbun.com/account/api. Strongly recommend the per-domain
    # "API ACCESS" toggle inside each domain's detail page
    # (porkbun.com -> Domain Management -> click the domain -> toggle
    # API ACCESS on) so the master key's blast radius is limited to
    # the domains you've enabled.
    PORKBUN_API_KEY: str | None = None
    PORKBUN_SECRET_KEY: str | None = None

    # Apex domain managed by the registrar adapter. Used by the `dns`
    # CLI as the default -d/--domain so commands like `dns set app`
    # know which registered domain to touch. Leave unset and the CLI
    # errors with a clear message.
    BASE_DOMAIN: str | None = None

    # Communications Service Configuration
    # Email (Resend) - Sign up at https://resend.com (free: 100 emails/day)
    RESEND_API_KEY: str | None = None
    # Sender address for every email this app sends (auth verification,
    # password reset, payment receipts, etc.). Two formats accepted -
    # the Resend SDK parses both:
    #
    #   1. Bare:           hello@your-domain.com
    #   2. Display + addr: "Your Brand <hello@your-domain.com>"
    #
    # Format (2) is recommended in production - inboxes render the
    # display name as the sender. The local-part doesn't need to exist
    # as a real inbox; only the domain needs to be verified at
    # resend.com/domains. Falling back to ``onboarding@resend.dev`` is
    # the shared sandbox address and only delivers to the email you
    # signed up to Resend with - every other recipient 403s.
    RESEND_FROM_EMAIL: str | None = None
    # Reply-To header attached to outbound mail. Lets recipients click
    # "Reply" and reach a real inbox even when the FROM lives on a
    # send-only verified subdomain (e.g. FROM=hello@send.example.com
    # but replies should land at hello@example.com forwarded to your
    # support inbox). Leave unset to let replies go to the FROM
    # address (where they'll bounce unless that mailbox exists).
    SUPPORT_REPLY_TO_EMAIL: str | None = None

    # SMS/Voice (Twilio) - Sign up at https://twilio.com/try-twilio ($15 trial)
    TWILIO_ACCOUNT_SID: str | None = None
    TWILIO_AUTH_TOKEN: str | None = None
    TWILIO_PHONE_NUMBER: str | None = None  # E.164 format: +15551234567
    TWILIO_MESSAGING_SERVICE_SID: str | None = None  # Required for toll-free SMS

    # Finance Service Configuration
    FINANCE_DEFAULT_CURRENCY: str = "usd"
    # How far back the reconciliation alerts look, in days. Transfer
    # suggestions, fee alerts, and missed-bill checks only consider activity
    # inside this window of today, so a deep historical import populates the
    # ledger without burying Review/Insights under years of stale findings.
    # 0 disables the window (full history). Rules with their own windows
    # (recurring detection, overspend, large transactions) are unaffected.
    FINANCE_RULES_LOOKBACK_DAYS: int = 31
    # Provider capabilities built into this stack. Credentials are set
    # separately in .env; these flags say which connect flows exist at all,
    # so the UI can offer them (and prompt for missing credentials) rather
    # than hide the feature on a fresh project.
    FINANCE_PLAID: bool = True
    FINANCE_SNAPTRADE: bool = True
    # Plaid (bank/credit/investment linking). Sign up at
    # https://dashboard.plaid.com; sandbox keys work with no approval.
    PLAID_CLIENT_ID: str | None = None
    PLAID_SECRET: str | None = None
    PLAID_ENV: str = "sandbox"  # sandbox | production
    PLAID_WEBHOOK_URL: str | None = None
    PLAID_REDIRECT_URI: str | None = None
    # Set by the dev compose overlay: the cloudflared quick-tunnel sidecar's
    # metrics address. When present, the backend discovers the tunnel's public
    # hostname at startup and routes Plaid webhooks through it.
    PLAID_TUNNEL_METRICS_URL: str | None = None
    # SnapTrade (brokerage linking — the Fidelity path). Application-gated.
    SNAPTRADE_CLIENT_ID: str | None = None
    SNAPTRADE_CONSUMER_KEY: str | None = None

    # Scheduler settings
    SCHEDULER_TIMEZONE: str = "UTC"  # IANA timezone name; cron triggers inherit this

    @property
    def is_docker(self) -> bool:
        """Detect if running inside Docker container."""
        import os

        return os.path.exists("/.dockerenv") or bool(os.getenv("DOCKER_CONTAINER"))

    @staticmethod
    def _localhost_url(
        url: str, docker_hostnames: set[str], host_port: int | None = None
    ) -> str:
        """Rewrite a Docker-network URL for access from the host.

        Swaps a Docker service hostname (e.g. ``postgres``) for
        ``localhost`` and, when ``make serve`` chose a non-default host
        publish port (a ``*_HOST_PORT`` loaded from ``.env.ports``), swaps
        the container port for it. ``make serve`` shifts only the host
        publish port when the default is taken by another stack, so a
        host-side CLI command must use that chosen port, not the container
        port. Returns ``url`` unchanged when its host is not one of
        ``docker_hostnames`` (already host-reachable). Auth and path are
        preserved.
        """
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(url)
        if parsed.hostname not in docker_hostnames:
            return url
        netloc = "localhost"
        if parsed.username:
            if parsed.password:
                netloc = f"{parsed.username}:{parsed.password}@localhost"
            else:
                netloc = f"{parsed.username}@localhost"
        port = host_port or parsed.port
        if port:
            netloc = f"{netloc}:{port}"
        return urlunparse(parsed._replace(netloc=netloc))

    @property
    def ollama_base_url_effective(self) -> str:
        """Get effective Ollama URL, preferring local override when not in Docker."""
        if self.OLLAMA_BASE_URL_LOCAL and not self.is_docker:
            return self.OLLAMA_BASE_URL_LOCAL
        if not self.is_docker:
            return self._localhost_url(
                self.OLLAMA_BASE_URL,
                {"ollama", "host.docker.internal"},
                self.OLLAMA_HOST_PORT,
            )
        return self.OLLAMA_BASE_URL

    # ``.env.ports`` carries the host publish ports `make serve` chose for
    # this run (gitignored, regenerated each run). Listed after ``.env`` so
    # its values win for the keys it defines; both are optional at load time.
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.ports"), env_file_encoding="utf-8"
    )

    @model_validator(mode="after")
    def _guard_against_placeholder_secret_in_prod(self) -> Settings:
        """Refuse to boot a prod-shaped deploy with the template SECRET_KEY.

        Anyone reading the public template knows this value, so leaving it
        unchanged would let an attacker forge JWTs against the deploy. We
        only enforce when APP_ENV != "dev" so local development keeps
        working without forcing every contributor to set the env var.
        """
        if self.APP_ENV != "dev" and self.SECRET_KEY == _SECRET_KEY_PLACEHOLDER:
            raise ValueError(
                "SECRET_KEY is set to the template placeholder. Generate a "
                "random value (e.g. `python -c 'import secrets;"
                " print(secrets.token_urlsafe(64))'`) and set it via the "
                "SECRET_KEY env var before booting outside dev."
            )
        return self

    @model_validator(mode="after")
    def _guard_plaid_production_keys(self) -> Settings:
        """Require real Plaid credentials before running against production.

        ``PLAID_ENV=production`` hits real financial institutions; booting
        without keys would fail opaquely at first sync, so fail fast at startup.
        """
        if self.PLAID_ENV == "production" and not (
            self.PLAID_CLIENT_ID and self.PLAID_SECRET
        ):
            raise ValueError(
                "PLAID_ENV=production requires PLAID_CLIENT_ID and PLAID_SECRET "
                "to be set. Use PLAID_ENV=sandbox for local development."
            )
        return self

    def reload(self) -> None:
        """
        Reload settings from .env file in place.

        Updates all field values from a fresh Settings instance,
        allowing configuration changes to take effect without restart.
        """
        # Create a fresh Settings instance that reads from .env
        new_settings = Settings()

        # Update all field values in place
        for field_name in self.model_fields:
            setattr(self, field_name, getattr(new_settings, field_name))


settings = Settings()


def reload_settings() -> None:
    """
    Reload the global settings instance from .env file.

    Call this after modifying the .env file to pick up changes
    without restarting the application.
    """
    settings.reload()


# Pure arq queue helper functions - use dynamic discovery
def get_available_queues() -> list[str]:
    """Get all available queue names via dynamic discovery."""
    try:
        from app.components.worker.registry import discover_worker_queues

        queues: list[str] = discover_worker_queues()
        return queues
    except ImportError:
        # Worker components not available
        return []


def get_default_queue() -> str:
    """Get the default queue name for load testing."""
    # Prefer load_test queue if it exists, otherwise use first available
    available = get_available_queues()
    if "load_test" in available:
        return "load_test"
    return available[0] if available else "system"


def get_load_test_queue() -> str:
    """Get the queue name for load testing."""
    available = get_available_queues()
    return "load_test" if "load_test" in available else get_default_queue()


def is_valid_queue(queue_name: str) -> bool:
    """Check if a queue name is valid."""
    try:
        from app.components.worker.registry import validate_queue_name

        result: bool = validate_queue_name(queue_name)
        return result
    except ImportError:
        # Worker components not available, no queues are valid
        return False
