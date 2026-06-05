"""MCP tools for Odoo operations."""

from collections.abc import Iterable

from .employee import EMPLOYEE_TOOLS, execute_employee_tool
from .records import TOOLS as CRUD_TOOLS
from .records import execute_tool as execute_crud_tool
from .sign import SIGN_TOOLS, execute_sign_tool

__all__ = [
    "CRUD_TOOLS",
    "EMPLOYEE_TOOLS",
    "SIGN_TOOLS",
    "TOOL_GROUPS",
    "DEFAULT_TOOL_GROUPS",
    "execute_crud_tool",
    "execute_employee_tool",
    "execute_sign_tool",
    "register_tools",
    "register_employee_tools",
    "tool_group_for",
    "execute_tool",
]

# Tool groups that can be enabled/disabled per deployment (see ENABLED_TOOL_GROUPS).
TOOL_GROUPS: dict[str, list] = {
    "crud": CRUD_TOOLS,
    "employee": EMPLOYEE_TOOLS,
    "sign": SIGN_TOOLS,
}

# Stock default when nothing is configured.
DEFAULT_TOOL_GROUPS = ("crud", "employee")

# Reverse lookup: tool name -> group.
_TOOL_NAME_TO_GROUP = {
    tool.name: group for group, tools in TOOL_GROUPS.items() for tool in tools
}


def tool_group_for(name: str) -> str | None:
    """Return the group ('crud' | 'employee' | 'sign') a tool belongs to, or None."""
    return _TOOL_NAME_TO_GROUP.get(name)


def register_tools(groups: Iterable[str] = DEFAULT_TOOL_GROUPS):
    """Return the tools for the enabled groups, in stable group order.

    ``groups`` selects among 'crud', 'employee', and 'sign'. Sign tools also
    require the optional OCA `sign_oca` addon at runtime.
    """
    groups = set(groups)
    tools: list = []
    for group in ("crud", "employee", "sign"):
        if group in groups:
            tools = tools + TOOL_GROUPS[group]
    return tools


def register_employee_tools(groups: Iterable[str] = ("employee", "sign")):
    """Return employee self-service tools for the enabled groups."""
    groups = set(groups)
    tools: list = []
    for group in ("employee", "sign"):
        if group in groups:
            tools = tools + TOOL_GROUPS[group]
    return tools


async def execute_tool(name: str, arguments: dict, odoo_client):  # type: ignore[type-arg]
    """
    Execute a tool by name (CRUD tools only).

    Employee tools require employee context and should be called via
    execute_employee_tool directly with the employee_id parameter.
    """
    # Employee tools require employee context - raise error
    employee_tool_names = [t.name for t in EMPLOYEE_TOOLS]
    if name in employee_tool_names:
        raise ValueError(f"Employee tool '{name}' requires employee context. Use execute_employee_tool instead.")

    # Sign tools require employee context - raise error
    sign_tool_names = [t.name for t in SIGN_TOOLS]
    if name in sign_tool_names:
        raise ValueError(f"Sign tool '{name}' requires employee context. Use execute_sign_tool instead.")

    # Execute CRUD tools
    crud_tool_names = [t.name for t in CRUD_TOOLS]
    if name in crud_tool_names:
        return await execute_crud_tool(name, arguments, odoo_client)

    raise ValueError(f"Unknown tool: {name}")
