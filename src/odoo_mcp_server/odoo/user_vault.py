"""Per-user Odoo credential lookup.

Wraps ``odoo_mcp_bridge.vault.EncryptedFileVault`` (Fernet-encrypted, single
JSON file) to store each user's OWN (odoo_login, odoo_api_key) pair, keyed by
their OAuth email. Populated manually via scripts/set_user_odoo_key.py.

When ``handle_tools_call`` finds an entry here for the caller, generic CRUD
tools authenticate to Odoo AS THAT USER instead of the shared service
account - Odoo's own permissions (ir.model.access, record rules) decide what
they can do, bypassing CRUD_ADMIN_EMAILS for that call. Users without an
entry are unaffected: existing scope-gated, shared-account behavior.
"""

from __future__ import annotations

import json

from odoo_mcp_bridge.vault import EncryptedFileVault


class UserCredentialVault:
    """Stores/retrieves a user's own (odoo_login, odoo_api_key) by email."""

    def __init__(self, path: str, encryption_key: str) -> None:
        self._vault = EncryptedFileVault(path, encryption_key)

    def get_credentials(self, email: str) -> tuple[str, str] | None:
        """Return (odoo_login, odoo_api_key) for ``email``, or None if absent/invalid."""
        raw = self._vault.get_key(email)
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        login = payload.get("odoo_login")
        api_key = payload.get("odoo_api_key")
        if not login or not api_key:
            return None
        return login, api_key

    def put_credentials(self, email: str, odoo_login: str, odoo_api_key: str) -> None:
        """Store ``email``'s own Odoo login + API key."""
        self._vault.put_key(email, json.dumps({"odoo_login": odoo_login, "odoo_api_key": odoo_api_key}))

    def delete_credentials(self, email: str) -> None:
        self._vault.delete_key(email)


def build_user_vault(settings) -> UserCredentialVault | None:
    """Build the vault from settings, or None if the feature isn't configured.

    Both USER_KEY_VAULT_PATH and USER_KEY_VAULT_ENCRYPTION_KEY must be set;
    unset by default so existing deployments are unaffected.
    """
    path = getattr(settings, "user_key_vault_path", None)
    key = getattr(settings, "user_key_vault_encryption_key", None)
    if not path or not key:
        return None
    return UserCredentialVault(path, key)
