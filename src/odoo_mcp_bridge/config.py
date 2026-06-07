"""Environment-driven configuration for the Odoo MCP per-user-identity bridge.

Required values fail fast at startup (no silent defaults for secrets/identity). Reuses
the Odoo connection vars (`ODOO_URL`/`ODOO_DB`/`ODOO_API_KEY`/`ODOO_USERNAME`) for the
admin/service account used to mint per-user keys, and the existing
`TOKEN_STORAGE_BACKEND`/`TOKEN_ENCRYPTION_KEY` knobs for the per-user key vault.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("odoo_mcp_bridge")


def _require(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(
            f"{name} is not set. It is required to start the Odoo MCP bridge. "
            "See README / .env.example."
        )
    return value


def _split_csv(value: str) -> list[str]:
    return [item.strip().lower() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class BridgeConfig:
    """Resolved bridge configuration."""

    # Odoo connection + admin/service account (used to mint per-user keys).
    odoo_url: str
    odoo_db: str
    odoo_service_username: str
    odoo_service_api_key: str

    # Google OAuth (FastMCP GoogleProvider).
    google_client_id: str
    google_client_secret: str
    bridge_public_url: str
    session_secret: str

    # Allowlist.
    allowed_emails: list[str]
    allowed_domains: list[str]

    # Per-user key vault.
    storage_backend: str  # "memory" | "encrypted_file" | "gcp_secret_manager"
    token_encryption_key: str | None
    token_store_path: str
    gcp_project: str | None
    key_ttl_days: int

    host: str
    port: int

    @property
    def odoo_base(self) -> str:
        return self.odoo_url.rstrip("/")

    @property
    def allow_all_emails(self) -> bool:
        return "*" in self.allowed_emails or "*" in self.allowed_domains

    def is_email_allowed(self, email: str) -> bool:
        """Allowlist check. With '*', any Google-authenticated email passes (the Odoo key
        becomes the real gate); otherwise only listed emails/domains pass."""
        email = (email or "").strip().lower()
        if not email:
            return False
        if self.allow_all_emails:
            return True
        if email in self.allowed_emails:
            return True
        domain = email.rsplit("@", 1)[-1] if "@" in email else ""
        return bool(domain) and domain in self.allowed_domains


def load_config() -> BridgeConfig:
    """Load and validate bridge configuration from the environment."""
    port = int(os.environ.get("PORT") or os.environ.get("BRIDGE_PORT", "8080"))

    allowed_emails = _split_csv(os.environ.get("BRIDGE_ALLOWED_EMAILS", ""))
    allowed_domains = _split_csv(os.environ.get("BRIDGE_ALLOWED_DOMAINS", ""))
    if not allowed_emails and not allowed_domains:
        raise RuntimeError(
            "Set BRIDGE_ALLOWED_EMAILS and/or BRIDGE_ALLOWED_DOMAINS to control who may "
            "connect (use '*' to allow any Google account). Refusing to start with an "
            "unset allowlist."
        )
    if "*" in allowed_emails or "*" in allowed_domains:
        logger.warning(
            "Allowlist is OPEN ('*'): any Google-authenticated user may connect. "
            "Access is gated only by each user's Odoo permissions."
        )

    backend = os.environ.get("TOKEN_STORAGE_BACKEND", "memory").strip().lower()
    enc_key = os.environ.get("TOKEN_ENCRYPTION_KEY") or None
    if backend == "encrypted_file" and not enc_key:
        raise RuntimeError(
            "TOKEN_STORAGE_BACKEND=encrypted_file requires TOKEN_ENCRYPTION_KEY "
            "(generate with: python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())')."
        )

    return BridgeConfig(
        odoo_url=_require("ODOO_URL"),
        odoo_db=_require("ODOO_DB"),
        odoo_service_username=_require("ODOO_USERNAME"),
        odoo_service_api_key=_require("ODOO_API_KEY"),
        google_client_id=_require("OAUTH_CLIENT_ID"),
        google_client_secret=_require("OAUTH_CLIENT_SECRET"),
        bridge_public_url=_require("OAUTH_RESOURCE_IDENTIFIER").rstrip("/"),
        session_secret=_require("SESSION_SECRET"),
        allowed_emails=allowed_emails,
        allowed_domains=allowed_domains,
        storage_backend=backend,
        token_encryption_key=enc_key,
        token_store_path=os.environ.get("TOKEN_STORE_PATH", "/tmp/odoo-mcp-keys.json"),  # nosec B108
        gcp_project=os.environ.get("GCP_PROJECT") or None,
        key_ttl_days=int(os.environ.get("ODOO_KEY_TTL_DAYS", "30")),
        host=os.environ.get("BRIDGE_HOST", "0.0.0.0"),  # nosec B104
        port=port,
    )
