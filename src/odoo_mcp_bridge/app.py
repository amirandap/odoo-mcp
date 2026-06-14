"""Build the bridge ASGI application (FastMCP + Starlette).

Serves:
- ``/mcp``      the FastMCP endpoint (Google OAuth via OdooVaultGoogleProvider; Odoo tools
                executed per-user), plus OAuth well-known/authorize/callback routes.
- ``/health``   liveness/readiness probe.

NOTE: wrapping the existing Odoo tools so each runs with the caller's per-user client is
Phase 4 (``register_odoo_tools``); Phase 1 wires auth + health + ``enrollment_status``.
"""

from __future__ import annotations

import logging

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from .auth import OdooVaultGoogleProvider
from .config import BridgeConfig
from .theme import DarkModeMiddleware
from .vault import Vault, build_vault

logger = logging.getLogger("odoo_mcp_bridge")


def build_mcp(config: BridgeConfig, vault: Vault) -> FastMCP:
    """Construct the FastMCP server with Google OAuth + per-user Odoo key vault."""
    from .oauth_storage import build_client_storage

    # Shared, persistent OAuth-proxy state (registered clients + refresh tokens). None =>
    # FastMCP's default local disk, which is ephemeral/per-instance on Cloud Run. See
    # oauth_storage.py — set OAUTH_CLIENT_STORAGE=firestore for a multi-instance deployment.
    auth = OdooVaultGoogleProvider(
        vault=vault,
        config=config,
        is_email_allowed=config.is_email_allowed,
        client_id=config.google_client_id,
        client_secret=config.google_client_secret,
        base_url=config.bridge_public_url,
        redirect_path="/auth/callback",
        required_scopes=["openid", "email"],
        client_storage=build_client_storage(config),
    )
    mcp = FastMCP(
        "Odoo (per-user)",
        auth=auth,
        instructions=(
            "Odoo ERP access where each user acts as themselves via their own Odoo API "
            "key (native permissions + correct authorship). If tools return auth errors, "
            "your Odoo account may not be linked yet — see enrollment_status."
        ),
    )

    @mcp.tool
    def enrollment_status() -> str:
        """Report whether the current user's Odoo identity is linked."""
        token = get_access_token()
        email = ((token.claims or {}).get("email") if token else None) or ""
        linked = bool(token and (token.claims or {}).get("auth_method") == "odoo_api_key")
        if linked:
            return f"Connected as {email}. Odoo tools will act as you."
        return (
            f"Authenticated as {email or 'unknown'}, but no Odoo key is linked yet. "
            "Ensure you have an internal Odoo user and that the administrator has installed "
            "the 'mcp_apikey_provisioning' addon, then retry."
        )

    from .tools_adapter import register_odoo_tools

    register_odoo_tools(mcp, config)
    return mcp


def build_app(config: BridgeConfig, vault: Vault | None = None) -> Starlette:
    """Build the full Starlette application."""
    if vault is None:
        vault = build_vault(config)

    mcp = build_mcp(config, vault)
    mcp_app = mcp.http_app(stateless_http=True)

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "mode": "per_user_identity"})

    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/healthz", health, methods=["GET"]),
        Mount("/", app=mcp_app),
    ]
    # DarkModeMiddleware themes the FastMCP consent page to the OS light/dark setting;
    # it only rewrites text/html, so MCP streaming responses pass through untouched.
    return Starlette(
        routes=routes,
        middleware=[Middleware(DarkModeMiddleware)],
        lifespan=lambda app: mcp_app.lifespan(mcp_app),  # type: ignore[arg-type]
    )
