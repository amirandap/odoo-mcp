"""
Odoo MCP Server

Main entry point for the MCP server with OAuth 2.1 support.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.types import (
    Resource,
    ResourcesCapability,
    ServerCapabilities,
    TextContent,
    Tool,
    ToolsCapability,
)

from .config import Settings
from .odoo.client import OdooClient
from .resources import register_resources
from .tools import register_tools
from .tools.employee import configure as configure_employee_tools

logger = logging.getLogger(__name__)

# Initialize server
server = Server("odoo-mcp-server")
settings = Settings()  # type: ignore[call-arg]  # pydantic-settings loads from env vars
odoo_client: OdooClient | None = None

# Apply per-instance tool configuration (custom employee fields, DMS folder names).
configure_employee_tools(
    custom_fields=settings.employee_custom_fields,
    dms_allowed_folders=settings.dms_allowed_folders_list,
    dms_restricted_folders=settings.dms_restricted_folders_list,
)


@asynccontextmanager
async def lifespan():
    """Manage server lifecycle"""
    global odoo_client

    odoo_client = OdooClient(
        url=settings.odoo_url,
        db=settings.odoo_db,
        api_key=settings.odoo_api_key,
        username=settings.odoo_username,
        password=settings.odoo_password
    )

    yield

    if odoo_client:
        await odoo_client.close()


@server.list_tools()
async def list_tools() -> list[Tool]:
    """Return available tools"""
    return register_tools(settings.effective_tool_groups)


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Execute a tool"""
    from .tools import execute_tool
    if not odoo_client:
        raise RuntimeError("Odoo client not initialized")
    return await execute_tool(name, arguments, odoo_client)


@server.list_resources()
async def list_resources() -> list[Resource]:
    """Return available resources"""
    return register_resources()


@server.read_resource()
async def read_resource(uri: str) -> str:
    """Read a resource"""
    from .resources import read_resource
    if not odoo_client:
        raise RuntimeError("Odoo client not initialized")
    return await read_resource(uri, odoo_client)


def main():
    """Main entry point"""
    import mcp.server.stdio

    async def run():
        async with lifespan():
            async with mcp.server.stdio.stdio_server() as (read, write):
                await server.run(
                    read, write,
                    InitializationOptions(
                        server_name="odoo-mcp-server",
                        server_version="0.1.0",
                        capabilities=ServerCapabilities(
                            tools=ToolsCapability(listChanged=False),
                            resources=ResourcesCapability(subscribe=False, listChanged=False),
                        ),
                    )
                )

    asyncio.run(run())


if __name__ == "__main__":
    main()
