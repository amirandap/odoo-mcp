"""Entry point: run the per-user-identity bridge with uvicorn."""

from __future__ import annotations

import logging
import sys


def _preflight(config) -> None:
    """Best-effort startup check: warn loudly if the companion addon is missing.

    Never blocks startup (the bridge still serves /health and enrollment_status, and Odoo
    tools fail with a clear message), but makes the hard dependency obvious in the logs.
    """
    import asyncio

    from .clients import build_admin_client
    from .provisioning import REQUIRED_ODOO_ADDON, is_addon_installed

    log = logging.getLogger("odoo_mcp_bridge")
    try:
        installed = asyncio.run(is_addon_installed(build_admin_client(config)))
    except Exception as exc:  # noqa: BLE001
        log.warning("Preflight: could not reach Odoo to verify dependencies (%s)", type(exc).__name__)
        return
    if installed:
        log.info("Preflight: required Odoo addon '%s' is installed.", REQUIRED_ODOO_ADDON)
    else:
        log.warning(
            "Preflight: required Odoo addon '%s' is NOT installed (or service account "
            "'%s' lacks access). Per-user identity will not work until the addon is "
            "deployed. See docs/odoo-team-instructions.md.",
            REQUIRED_ODOO_ADDON,
            config.odoo_service_username,
        )


def main() -> None:
    import uvicorn

    from .app import build_app
    from .config import load_config

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    config = load_config()
    _preflight(config)
    app = build_app(config)
    logging.getLogger("odoo_mcp_bridge").info(
        "Starting Odoo MCP per-user bridge on %s:%s (MCP at %s/mcp)",
        config.host,
        config.port,
        config.bridge_public_url,
    )
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
