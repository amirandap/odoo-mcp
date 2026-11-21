"""
ClickUp Tools (optional "clickup" tool group)

Native ClickUp tools backed by ``odoo_mcp_server.clickup``. Every tool accepts an
optional ``account`` naming one of the accounts configured in CLICKUP_ACCOUNTS
(default: CLICKUP_DEFAULT_ACCOUNT, else the first one). A failing account is never
retried with another one.
"""

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from mcp.types import TextContent, Tool

from ..clickup import ClickUpClient, ClickUpError
from ..clickup import operations as ops

logger = logging.getLogger(__name__)

_ACCOUNT = {
    "type": "string",
    "description": "ClickUp account name from CLICKUP_ACCOUNTS (omit for the default account). "
                   "A failing account is never retried with another one.",
}
_LIST_REF = "List ID, path 'Space/Folder/List' or unique list name"
_PEOPLE = {"type": "array", "items": {"type": "string"},
           "description": "Member IDs, emails, usernames or 'me'"}
_STRINGS = {"type": "array", "items": {"type": "string"}}
_DATE = "YYYY-MM-DD, ISO-8601, today, tomorrow or 'in N days'"


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    schema: dict[str, Any] = {"type": "object", "properties": {"account": _ACCOUNT, **properties}}
    if required:
        schema["required"] = required
    return schema


CLICKUP_TOOLS = [
    Tool(
        name="clickup_whoami",
        description="ClickUp user of the chosen account and the Workspace members (IDs, usernames, "
                    "emails). Useful to resolve assignees.",
        inputSchema=_schema({}),
    ),
    Tool(
        name="clickup_hierarchy",
        description="Spaces -> Folders -> Lists tree of the Workspace, with IDs. Use it before creating "
                    "or filtering. Cached 5 min; refresh=true re-reads it. Lists can then be referenced "
                    "by ID, path 'Space/Folder/List' or unique name.",
        inputSchema=_schema({
            "space": {"type": "string", "description": "Filter by Space name or ID"},
            "refresh": {"type": "boolean", "default": False},
        }),
    ),
    Tool(
        name="clickup_find_tasks",
        description="Search Workspace tasks with filters; returns compact rows. `text` is a substring "
                    "of the task name: ClickUp has no server-side text search, so up to 500 tasks are "
                    "scanned and the answer says whether the search was complete. Paging: `page` "
                    "(100 tasks per ClickUp page) and `has_more`.",
        inputSchema=_schema({
            "list": {"type": "string", "description": _LIST_REF},
            "space": {"type": "string", "description": "Space name or ID (when no list is given)"},
            "statuses": {**_STRINGS, "description": "Status names (validated against the list when given)"},
            "assignees": _PEOPLE,
            "tags": {**_STRINGS, "description": "Tag names"},
            "due_from": {"type": "string", "description": f"Due on/after: {_DATE}"},
            "due_to": {"type": "string", "description": f"Due on/before: {_DATE}"},
            "overdue": {"type": "boolean", "default": False},
            "text": {"type": "string", "description": "Substring of the task name"},
            "include_closed": {"type": "boolean", "default": False},
            "subtasks": {"type": "boolean", "default": False},
            "order_by": {"type": "string", "enum": list(ops._ORDER_BY), "default": "updated"},
            "page": {"type": "integer", "default": 0},
            "limit": {"type": "integer", "default": 50, "description": "Max 100"},
        }),
    ),
    Tool(
        name="clickup_get_task",
        description="Task detail: markdown description, status, assignees, dates, custom fields with a "
                    "value, checklists and subtasks.",
        inputSchema=_schema({
            "task_id": {"type": "string"},
            "include_subtasks": {"type": "boolean", "default": True},
            "include_comments": {"type": "boolean", "default": False},
            "custom_task_id": {"type": "boolean", "default": False,
                               "description": "true when task_id is a custom task ID"},
        }, ["task_id"]),
    ),
    Tool(
        name="clickup_create_task",
        description="Create a task (mutation: happens on call). `description` accepts markdown. "
                    "`parent` creates a subtask. `custom_fields`: {name_or_id: value}; drop-downs and "
                    "labels accept the option name.",
        inputSchema=_schema({
            "list": {"type": "string", "description": _LIST_REF},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "status": {"type": "string"},
            "assignees": _PEOPLE,
            "due_date": {"type": "string", "description": f"{_DATE} (date only = no time)"},
            "start_date": {"type": "string", "description": _DATE},
            "priority": {"type": "string", "enum": ["", "urgent", "high", "normal", "low"]},
            "tags": _STRINGS,
            "parent": {"type": "string", "description": "Parent task ID"},
            "custom_fields": {"type": "object"},
        }, ["list", "name"]),
    ),
    Tool(
        name="clickup_update_task",
        description="Edit a task (mutation). Only the given fields change. `status` is validated against "
                    "the task's list (the destination if moved). Dates accept 'none' to clear; "
                    "priority='none' clears it. `move_to_list` is verified by re-reading the task and "
                    "fails if the move didn't happen. Returns the re-read task.",
        inputSchema=_schema({
            "task_id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "status": {"type": "string"},
            "priority": {"type": "string", "enum": ["urgent", "high", "normal", "low", "none"]},
            "due_date": {"type": "string", "description": f"{_DATE} or 'none'"},
            "start_date": {"type": "string", "description": f"{_DATE} or 'none'"},
            "add_assignees": _PEOPLE,
            "remove_assignees": _PEOPLE,
            "add_tags": _STRINGS,
            "remove_tags": _STRINGS,
            "move_to_list": {"type": "string", "description": _LIST_REF},
            "archived": {"type": "boolean"},
        }, ["task_id"]),
    ),
    Tool(
        name="clickup_set_custom_field",
        description="Set a task's custom field (mutation). `field`: name or ID. Drop-downs/labels accept "
                    "the option name; dates YYYY-MM-DD. value=null clears the field.",
        inputSchema=_schema({
            "task_id": {"type": "string"},
            "field": {"type": "string"},
            "value": {"description": "New value, or null to clear"},
        }, ["task_id", "field"]),
    ),
    Tool(
        name="clickup_list_comments",
        description="Comments of a task, most recent first (max 100).",
        inputSchema=_schema({
            "task_id": {"type": "string"},
            "limit": {"type": "integer", "default": 25},
        }, ["task_id"]),
    ),
    Tool(
        name="clickup_add_comment",
        description="Add a comment to a task (mutation). `assignee` assigns the comment to a person; "
                    "notify_all=true notifies every watcher, author included.",
        inputSchema=_schema({
            "task_id": {"type": "string"},
            "text": {"type": "string"},
            "notify_all": {"type": "boolean", "default": False},
            "assignee": {"type": "string", "description": "Member ID, email, username or 'me'"},
        }, ["task_id", "text"]),
    ),
    Tool(
        name="clickup_checklist",
        description="Checklists of a task. `list` only reads; the rest are mutations. `add` creates a "
                    "checklist named `name`; `add_item` adds `name` to `checklist` (name or ID, optional "
                    "when the task has one); check/uncheck/remove_item act on `item` (exact name or ID); "
                    "rename_item sets its text to `name`.",
        inputSchema=_schema({
            "task_id": {"type": "string"},
            "action": {"type": "string", "enum": ["list", "add", "add_item", "check", "uncheck",
                                                  "rename_item", "remove_item"], "default": "list"},
            "checklist": {"type": "string"},
            "item": {"type": "string"},
            "name": {"type": "string"},
        }, ["task_id"]),
    ),
]

def _hierarchy(c: ClickUpClient, account: str, a: dict) -> dict:
    data = c.hierarchy(account, refresh=bool(a.get("refresh", False)))
    spaces = data["spaces"]
    if a.get("space"):
        target = c.resolve_space(account, a["space"])
        spaces = [sp for sp in spaces if sp["id"] == target["id"]]
    return {"account": account, "workspace_id": data["workspace_id"], "spaces": spaces}


def _pick(a: dict, *keys: str) -> dict:
    return {k: a[k] for k in keys if k in a}


_HANDLERS: dict[str, Callable[[ClickUpClient, str, dict], dict]] = {
    "clickup_whoami": lambda c, acc, a: c.whoami(acc),
    "clickup_hierarchy": _hierarchy,
    "clickup_find_tasks": lambda c, acc, a: ops.find_tasks(
        c, acc, list_ref=a.get("list", ""), **_pick(
            a, "space", "statuses", "assignees", "tags", "due_from", "due_to", "overdue", "text",
            "include_closed", "subtasks", "order_by", "page", "limit")),
    "clickup_get_task": lambda c, acc, a: ops.get_task(
        c, acc, a["task_id"], **_pick(a, "include_subtasks", "include_comments", "custom_task_id")),
    "clickup_create_task": lambda c, acc, a: ops.create_task(
        c, acc, list_ref=a["list"], name=a["name"], **_pick(
            a, "description", "status", "assignees", "due_date", "start_date", "priority", "tags",
            "parent", "custom_fields")),
    "clickup_update_task": lambda c, acc, a: ops.update_task(
        c, acc, a["task_id"], **_pick(
            a, "name", "description", "status", "priority", "due_date", "start_date", "add_assignees",
            "remove_assignees", "add_tags", "remove_tags", "move_to_list", "archived")),
    "clickup_set_custom_field": lambda c, acc, a: ops.set_custom_field(
        c, acc, a["task_id"], a["field"], a.get("value")),
    "clickup_list_comments": lambda c, acc, a: ops.list_comments(c, acc, a["task_id"], a.get("limit", 25)),
    "clickup_add_comment": lambda c, acc, a: ops.add_comment(
        c, acc, a["task_id"], a["text"], **_pick(a, "notify_all", "assignee")),
    "clickup_checklist": lambda c, acc, a: ops.checklist(
        c, acc, a["task_id"], a.get("action", "list"), **_pick(a, "checklist", "item", "name")),
}


def _run(name: str, arguments: dict, client: ClickUpClient) -> dict:
    account = client.check_account(arguments.get("account"))
    return _HANDLERS[name](client, account, arguments)


async def execute_clickup_tool(
    name: str,
    arguments: dict[str, Any],
    client: ClickUpClient | None,
) -> list[TextContent]:
    """Run a ClickUp tool in a worker thread (the client is synchronous).

    ClickUp and input errors are returned to the caller as ``{"error": ...}`` because
    they are actionable (ambiguous list, unknown status, ...) and never carry tokens.
    """
    if name not in _HANDLERS:
        raise ValueError(f"Unknown ClickUp tool: {name}")
    if client is None:
        raise ValueError("ClickUp is not configured on this server")
    try:
        result = await asyncio.to_thread(_run, name, arguments, client)
    except (ClickUpError, ValueError, KeyError) as e:
        message = f"Missing argument: {e}" if isinstance(e, KeyError) else str(e)
        logger.warning("ClickUp tool %s failed: %s", name, type(e).__name__)
        return [TextContent(type="text", text=json.dumps({"error": message}))]
    return [TextContent(type="text", text=json.dumps(result, default=str))]
