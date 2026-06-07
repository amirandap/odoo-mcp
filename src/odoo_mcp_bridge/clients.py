"""Odoo client factories for the bridge.

Reuses the existing ``odoo_mcp_server.odoo.client.OdooClient`` for both the admin/service
account (used to mint keys and resolve users) and per-user clients (which authenticate
with the user's own API key, so Odoo applies that user's permissions).
"""

from __future__ import annotations

from odoo_mcp_server.odoo.client import OdooClient


def build_admin_client(config) -> OdooClient:
    """Service-account client (used for minting keys and resolving res.users)."""
    return OdooClient(
        url=config.odoo_url,
        db=config.odoo_db,
        api_key=config.odoo_service_api_key,
        username=config.odoo_service_username,
    )


def build_user_client(config, login: str, api_key: str) -> OdooClient:
    """Per-user client that authenticates as ``login`` with the user's own API key."""
    return OdooClient(
        url=config.odoo_url,
        db=config.odoo_db,
        api_key=api_key,
        username=login,
    )
