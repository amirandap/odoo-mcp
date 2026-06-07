"""Resolve Odoo users and auto-mint per-user API keys via the companion addon.

Requires the ``mcp_apikey_provisioning`` addon installed on the Odoo server (it adds the
admin-only ``res.users.mcp_mint_apikey`` method). Until it is deployed, ``mint_user_key``
raises :class:`ProvisioningError` with a clear message.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("odoo_mcp_bridge.provisioning")

# Hard dependency: the companion Odoo addon that exposes res.users.mcp_mint_apikey.
# Must be installed on the Odoo server for per-user identity mode to work. See
# odoo_addons/mcp_apikey_provisioning/ and docs/odoo-team-instructions.md.
REQUIRED_ODOO_ADDON = "mcp_apikey_provisioning"


class ProvisioningError(RuntimeError):
    """Raised when a per-user key cannot be resolved or minted."""


async def is_addon_installed(admin_client: Any) -> bool:
    """Return True if the required companion addon is installed on the Odoo server."""
    try:
        rows = await admin_client.execute(
            "ir.module.module",
            "search_read",
            [["name", "=", REQUIRED_ODOO_ADDON], ["state", "=", "installed"]],
            fields=["id"],
            limit=1,
        )
        return bool(rows)
    except Exception as exc:  # noqa: BLE001 - never fail a probe
        logger.warning("Could not verify %s addon install state: %s", REQUIRED_ODOO_ADDON, type(exc).__name__)
        return False


async def resolve_odoo_uid(admin_client: Any, email: str) -> int | None:
    """Resolve an email to an internal res.users id (by login). Returns None if not found."""
    email = (email or "").strip().lower()
    if not email:
        return None
    rows = await admin_client.execute(
        "res.users",
        "search_read",
        [["login", "=ilike", email], ["share", "=", False]],
        fields=["id", "login"],
        limit=1,
    )
    return rows[0]["id"] if rows else None


async def mint_user_key(admin_client: Any, email: str, ttl_days: int) -> str:
    """Mint a fresh rpc-scoped Odoo API key for ``email`` via the companion addon.

    :raises ProvisioningError: if the user can't be resolved, the addon isn't installed,
        or the mint call otherwise fails.
    """
    uid = await resolve_odoo_uid(admin_client, email)
    if not uid:
        raise ProvisioningError(f"No internal Odoo user found for {email}.")

    try:
        key = await admin_client.execute(
            "res.users", "mcp_mint_apikey", uid, f"mcp:{email}", ttl_days
        )
    except Exception as exc:  # noqa: BLE001 - surface a clear, actionable message
        msg = str(exc)
        if "mcp_mint_apikey" in msg or "does not exist" in msg:
            raise ProvisioningError(
                f"Odoo did not accept mcp_mint_apikey. Is the '{REQUIRED_ODOO_ADDON}' "
                "addon installed and the service account a system administrator?"
            ) from exc
        raise ProvisioningError(f"Minting an Odoo API key for {email} failed: {type(exc).__name__}") from exc

    if not key or not isinstance(key, str):
        raise ProvisioningError(f"Mint returned no key for {email}.")
    logger.info("Minted per-user Odoo API key for an enrolled user (uid=%s)", uid)
    return key
