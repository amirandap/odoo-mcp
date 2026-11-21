"""MCP tools for Odoo operations."""

from collections.abc import Iterable

from .clickup import CLICKUP_TOOLS, execute_clickup_tool
from .consumption_report import TOOLS as REPORT_TOOLS
from .consumption_report import execute_tool as execute_report_tool
from .employee import EMPLOYEE_TOOLS, execute_employee_tool
from .records import TOOLS as CRUD_TOOLS
from .records import execute_tool as execute_crud_tool
from .sign import SIGN_TOOLS, execute_sign_tool

__all__ = [
    "CLICKUP_TOOLS",
    "CRUD_TOOLS",
    "EMPLOYEE_TOOLS",
    "SIGN_TOOLS",
    "REPORT_TOOLS",
    "TOOL_GROUPS",
    "DEFAULT_TOOL_GROUPS",
    "execute_crud_tool",
    "execute_employee_tool",
    "execute_sign_tool",
    "execute_report_tool",
    "execute_clickup_tool",
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
    "reports": REPORT_TOOLS,
    "clickup": CLICKUP_TOOLS,
}

# Stock default when nothing is configured.
DEFAULT_TOOL_GROUPS = ("crud", "employee")

# Reverse lookup: tool name -> group.
_TOOL_NAME_TO_GROUP = {
    tool.name: group for group, tools in TOOL_GROUPS.items() for tool in tools
}


def tool_group_for(name: str) -> str | None:
    """Return the group ('crud' | 'employee' | 'sign' | 'reports' | 'clickup') a tool belongs to, or None."""
    return _TOOL_NAME_TO_GROUP.get(name)


def register_tools(groups: Iterable[str] = DEFAULT_TOOL_GROUPS):
    """Return the tools for the enabled groups, in stable group order.

    ``groups`` selects among 'crud', 'employee', 'sign', 'reports' and 'clickup'.
    Sign tools also require the optional OCA `sign_oca` addon at runtime;
    ClickUp tools require CLICKUP_ACCOUNTS and CLICKUP_WORKSPACE_ID.
    """
    groups = set(groups)
    tools: list = []
    for group in ("crud", "employee", "sign", "reports", "clickup"):
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


async def execute_tool(name: str, arguments: dict, odoo_client, settings=None, clickup_client=None):  # type: ignore[type-arg]
    """
    Execute a tool by name (CRUD, report and ClickUp tools).

    Employee tools require employee context and should be called via
    execute_employee_tool directly with the employee_id parameter.

    ``settings`` is only required for report tools (see consumption_report.py) -
    generic CRUD callers can omit it. ``clickup_client`` is only required for
    ClickUp tools (see tools/clickup.py).
    """
    # Employee tools require employee context - raise error
    employee_tool_names = [t.name for t in EMPLOYEE_TOOLS]
    if name in employee_tool_names:
        raise ValueError(f"Employee tool '{name}' requires employee context. Use execute_employee_tool instead.")

    # Sign tools require employee context - raise error
    sign_tool_names = [t.name for t in SIGN_TOOLS]
    if name in sign_tool_names:
        raise ValueError(f"Sign tool '{name}' requires employee context. Use execute_sign_tool instead.")

    # Execute ClickUp tools (they never touch Odoo)
    if name in {t.name for t in CLICKUP_TOOLS}:
        return await execute_clickup_tool(name, arguments, clickup_client)

    # Execute report tools
    report_tool_names = [t.name for t in REPORT_TOOLS]
    if name in report_tool_names:
        if settings is None:
            raise ValueError(f"Report tool '{name}' requires settings for its client list.")
        return await execute_report_tool(
            name,
            arguments,
            odoo_client,
            report_groups=settings.consumption_report_groups,
            company_id=settings.consumption_report_company_id,
        )

    # Execute CRUD tools
    crud_tool_names = [t.name for t in CRUD_TOOLS]
    if name in crud_tool_names:
        return await execute_crud_tool(name, arguments, odoo_client)

    raise ValueError(f"Unknown tool: {name}")
