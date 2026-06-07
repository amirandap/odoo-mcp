"""Per-user Odoo API-key vault.

Stores each user's minted Odoo API key keyed by their (Google-verified) email. The
payload also carries the email (for a sanity check) and a `minted_at` timestamp. Key
values are never logged. Backends: in-memory (tests/dev), encrypted file (Fernet), and
GCP Secret Manager (one secret per user).

Adapted from plane-mcp-bridge's vault.py.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Protocol

# Storage ids must be charset-safe (Secret Manager: [A-Za-z0-9_-]{1,255}).
_ID_PREFIX = "odoo-key-"


def secret_id_for_email(email: str) -> str:
    """Deterministic, charset-safe id for a user's key."""
    digest = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()
    return f"{_ID_PREFIX}{digest[:40]}"


def _payload(email: str, api_key: str) -> dict:
    return {"email": email.strip().lower(), "api_key": api_key, "minted_at": int(time.time())}


def _key_if_fresh(payload: dict | None, max_age_seconds: float | None) -> str | None:
    """Return the stored api_key, or None if missing or older than max_age_seconds.

    Returning None for a too-old key makes the caller re-mint *before* the Odoo key's TTL
    actually expires (proactive rotation), so a long-lived deployment never serves a
    just-expired key.
    """
    if not payload:
        return None
    if max_age_seconds is not None and (time.time() - payload.get("minted_at", 0)) > max_age_seconds:
        return None
    return payload.get("api_key") or None


class Vault(Protocol):
    """Stores and retrieves a user's Odoo API key by email."""

    def get_key(self, email: str, max_age_seconds: float | None = None) -> str | None: ...

    def put_key(self, email: str, api_key: str) -> None: ...

    def delete_key(self, email: str) -> None: ...


class InMemoryVault:
    """Non-persistent vault for tests and local development."""

    def __init__(self) -> None:
        self._store: dict[str, dict] = {}

    def get_key(self, email: str, max_age_seconds: float | None = None) -> str | None:
        return _key_if_fresh(self._store.get(secret_id_for_email(email)), max_age_seconds)

    def put_key(self, email: str, api_key: str) -> None:
        self._store[secret_id_for_email(email)] = _payload(email, api_key)

    def delete_key(self, email: str) -> None:
        self._store.pop(secret_id_for_email(email), None)


class EncryptedFileVault:
    """Single-file vault with Fernet-encrypted payloads.

    The file maps `secret_id -> fernet_token`, where each token decrypts to the JSON
    payload `{email, api_key, minted_at}`. Suitable for single-instance deployments.
    """

    def __init__(self, path: str, encryption_key: str) -> None:
        from cryptography.fernet import Fernet

        self._path = Path(path)
        self._fernet = Fernet(encryption_key.encode() if isinstance(encryption_key, str) else encryption_key)
        self._lock = threading.Lock()

    def _read_all(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_all(self, data: dict[str, str]) -> None:
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data), "utf-8")
        tmp.replace(self._path)

    def get_key(self, email: str, max_age_seconds: float | None = None) -> str | None:
        from cryptography.fernet import InvalidToken

        token = self._read_all().get(secret_id_for_email(email))
        if not token:
            return None
        try:
            payload = json.loads(self._fernet.decrypt(token.encode()).decode("utf-8"))
        except (InvalidToken, json.JSONDecodeError):
            return None
        if payload.get("email", "").strip().lower() != email.strip().lower():
            return None
        return _key_if_fresh(payload, max_age_seconds)

    def put_key(self, email: str, api_key: str) -> None:
        token = self._fernet.encrypt(json.dumps(_payload(email, api_key)).encode("utf-8")).decode("utf-8")
        with self._lock:
            data = self._read_all()
            data[secret_id_for_email(email)] = token
            self._write_all(data)

    def delete_key(self, email: str) -> None:
        with self._lock:
            data = self._read_all()
            if data.pop(secret_id_for_email(email), None) is not None:
                self._write_all(data)


class SecretManagerVault:
    """GCP Secret Manager backed vault (one secret per user)."""

    def __init__(self, project: str, client=None) -> None:
        self._project = project
        if client is None:
            from google.cloud import secretmanager  # lazy import

            client = secretmanager.SecretManagerServiceClient()
        self._client = client

    def _parent(self) -> str:
        return f"projects/{self._project}"

    def _secret_name(self, email: str) -> str:
        return f"{self._parent()}/secrets/{secret_id_for_email(email)}"

    def get_key(self, email: str, max_age_seconds: float | None = None) -> str | None:
        from google.api_core.exceptions import NotFound

        name = f"{self._secret_name(email)}/versions/latest"
        try:
            response = self._client.access_secret_version(request={"name": name})
        except NotFound:
            return None
        payload = json.loads(response.payload.data.decode("utf-8"))
        if payload.get("email", "").strip().lower() != email.strip().lower():
            return None
        return _key_if_fresh(payload, max_age_seconds)

    def put_key(self, email: str, api_key: str) -> None:
        from google.api_core.exceptions import AlreadyExists

        secret_id = secret_id_for_email(email)
        try:
            self._client.create_secret(
                request={
                    "parent": self._parent(),
                    "secret_id": secret_id,
                    "secret": {"replication": {"automatic": {}}},
                }
            )
        except AlreadyExists:
            pass
        data = json.dumps(_payload(email, api_key)).encode("utf-8")
        self._client.add_secret_version(request={"parent": self._secret_name(email), "payload": {"data": data}})

    def delete_key(self, email: str) -> None:
        from google.api_core.exceptions import NotFound

        try:
            self._client.delete_secret(request={"name": self._secret_name(email)})
        except NotFound:
            pass


def build_vault(config) -> Vault:
    """Construct the vault backend named by config.storage_backend."""
    backend = config.storage_backend
    if backend == "memory":
        return InMemoryVault()
    if backend == "encrypted_file":
        return EncryptedFileVault(config.token_store_path, config.token_encryption_key)
    if backend in ("gcp_secret_manager", "secret_manager"):
        if not config.gcp_project:
            raise RuntimeError("GCP_PROJECT is required for the gcp_secret_manager storage backend.")
        return SecretManagerVault(project=config.gcp_project)
    raise RuntimeError(f"Unknown TOKEN_STORAGE_BACKEND: {backend!r}")
