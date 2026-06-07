"""Per-user identity bridge for the Odoo MCP server.

A FastMCP + Starlette app (adapted from plane-mcp-bridge) that authenticates the
caller via Google OAuth, resolves/auto-mints that user's Odoo API key, and performs
Odoo operations **as the user** (native record rules + correct create_uid) instead of
as the shared service account.

This is an independent entrypoint; the existing ``odoo_mcp_server.http_server`` remains
the shared-service-account server.
"""
