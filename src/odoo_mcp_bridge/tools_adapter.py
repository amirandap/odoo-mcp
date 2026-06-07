"""Expose the existing Odoo MCP tools through FastMCP, executed per-user.

Each existing tool (defined in ``odoo_mcp_server.tools`` as an MCP ``Tool`` + an
``execute_*`` dispatcher using a shared ``OdooClient``) is registered as a FastMCP tool
that, per call, builds a **per-user** ``OdooClient`` from the caller's minted Odoo API
key (carried on the FastMCP ``AccessToken``) and runs the same logic as that user.

So all tool behaviour and schemas are reused unchanged; only the Odoo *identity* differs
(native record rules + correct create_uid), versus the shared-service-account server.
"""

from __future__ import annotations

import logging

from fastmcp.server.dependencies import get_access_token
from fastmcp.tools.base import Tool
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

# Reused unchanged from the existing server.
from odoo_mcp_server.tools import (
    EMPLOYEE_TOOLS,
    SIGN_TOOLS,
    execute_employee_tool,
    execute_sign_tool,
    execute_tool,
    register_tools,
    tool_group_for,
)

from .clients import build_user_client

logger = logging.getLogger("odoo_mcp_bridge.tools")

_EMPLOYEE_TOOL_NAMES = {t.name for t in EMPLOYEE_TOOLS}
_SIGN_TOOL_NAMES = {t.name for t in SIGN_TOOLS}

# Single per-process bridge config, set by register_odoo_tools (one config per app).
_BRIDGE_CONFIG: object = None


async def _resolve_employee_id(user_client) -> int | None:
    """Resolve the authenticated user's own hr.employee id (their own record)."""
    uid = await user_client.authenticate()
    rows = await user_client.execute(
        "hr.employee", "search_read", [["user_id", "=", uid]], fields=["id"], limit=1
    )
    return rows[0]["id"] if rows else None


async def dispatch(config, tool_name: str, arguments: dict, email: str, api_key: str) -> list[TextContent]:
    """Execute ``tool_name`` as the user identified by (email, api_key).

    Returns the tool's TextContent list. Raises on Odoo errors (the caller wraps them).
    """
    client = build_user_client(config, login=email, api_key=api_key)
    try:
        group = tool_group_for(tool_name)
        if group in ("employee", "sign") or tool_name in _EMPLOYEE_TOOL_NAMES or tool_name in _SIGN_TOOL_NAMES:
            employee_id = await _resolve_employee_id(client)
            if not employee_id:
                return [TextContent(type="text", text="No Odoo employee record is linked to your account.")]
            if tool_name in _SIGN_TOOL_NAMES:
                return await execute_sign_tool(tool_name, arguments, client, employee_id)
            return await execute_employee_tool(tool_name, arguments, client, employee_id)
        # CRUD / generic tools — no employee context needed.
        return await execute_tool(tool_name, arguments, client)
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            try:
                await close()
            except Exception:  # noqa: BLE001 - best effort
                pass


class OdooProxyTool(Tool):
    """FastMCP tool that proxies to the existing Odoo tool logic, as the calling user."""

    async def run(self, arguments: dict) -> ToolResult:  # type: ignore[override]
        token = get_access_token()
        claims = (token.claims or {}) if token else {}
        email = (claims.get("email") or "").strip().lower()
        linked = bool(token and claims.get("auth_method") == "odoo_api_key")
        if not linked or not email:
            return ToolResult(
                content=[TextContent(
                    type="text",
                    text=("Your Odoo account is not linked yet (no per-user key). "
                          "See enrollment_status; ensure you are an internal Odoo user and the "
                          "administrator has installed the mcp_apikey_provisioning addon."),
                )]
            )
        assert token is not None  # guaranteed by `linked` above; narrows for type-checkers
        try:
            content = await dispatch(_BRIDGE_CONFIG, self.name, arguments, email, token.token)
        except Exception as exc:  # noqa: BLE001 - surface Odoo errors as tool output
            logger.info("Tool %s failed for a user: %s", self.name, type(exc).__name__)
            content = [TextContent(type="text", text=f"Odoo error running '{self.name}': {type(exc).__name__}")]
        return ToolResult(content=content)


def register_odoo_tools(mcp, config) -> int:
    """Register all enabled Odoo tools onto the FastMCP server. Returns the count."""
    global _BRIDGE_CONFIG
    _BRIDGE_CONFIG = config

    # Apply the existing per-instance tool configuration (custom fields, DMS folders).
    from odoo_mcp_server.config import Settings
    from odoo_mcp_server.tools.employee import configure as configure_employee_tools

    settings = Settings()  # type: ignore[call-arg]
    configure_employee_tools(
        custom_fields=settings.employee_custom_fields,
        dms_allowed_folders=settings.dms_allowed_folders_list,
        dms_restricted_folders=settings.dms_restricted_folders_list,
    )

    count = 0
    for tool in register_tools(settings.effective_tool_groups):
        proxy = OdooProxyTool(
            name=tool.name,
            description=tool.description or "",
            parameters=tool.inputSchema or {"type": "object", "properties": {}},
        )
        mcp.add_tool(proxy)
        count += 1
    logger.info("Registered %d Odoo tools (per-user) on the bridge.", count)
    return count
