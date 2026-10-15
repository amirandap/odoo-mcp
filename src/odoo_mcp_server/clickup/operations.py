"""ClickUp task, comment and checklist operations on top of ``ClickUpClient``.

Each function takes an already-validated account name (see
``ClickUpClient.check_account``) and returns a JSON-serializable dict.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

from .client import (
    _CHECKLIST_ACTIONS,
    _MAX_COMMENT_CHARS,
    _MAX_SCAN_PAGES,
    _PAGE_SIZE,
    ClickUpClient,
    ClickUpError,
    _match_field,
    _match_status,
    _norm,
    _priority_value,
    _shape_checklists,
    _task_id,
)

_ORDER_BY = ("updated", "created", "due_date")
_CLEAR = ("", "none", "clear")


# --------------------------------------------------------------------------- find


def _find_params(c: ClickUpClient, account: str, *, list_ref: str, space: str,
                 statuses: list[str] | None, assignees: list[str] | None, tags: list[str] | None,
                 due_from: str, due_to: str, overdue: bool, include_closed: bool,
                 subtasks: bool, order_by: str) -> tuple[list[tuple[str, Any]], str]:
    # Without `reverse` ClickUp sorts descending (most recent first); for due dates the
    # nearest one first is what's useful.
    params: list[tuple[str, Any]] = [("order_by", order_by)]
    if order_by == "due_date":
        params.append(("reverse", "true"))
    scope = "workspace"
    scoped_list = None
    if list_ref:
        scoped_list = c.resolve_list(account, list_ref)
        params.append(("list_ids[]", scoped_list["id"]))
        scope = scoped_list["path"]
    elif space:
        sp = c.resolve_space(account, space)
        params.append(("space_ids[]", sp["id"]))
        scope = f"space {sp['name']}"
    if statuses and scoped_list:
        valid = c.list_statuses(account, scoped_list["id"])
        statuses = [_match_status(s, valid) for s in statuses]
    params.extend(("statuses[]", s) for s in statuses or [])
    params.extend(("assignees[]", c.resolve_member(account, a)) for a in assignees or [])
    params.extend(("tags[]", tag) for tag in tags or [])
    if overdue:
        params.append(("due_date_lt", int(time.time() * 1000)))
    else:
        if due_from:
            params.append(("due_date_gt", c.parse_date(due_from)[0] - 1))
        if due_to:
            params.append(("due_date_lt", c.parse_date(due_to, end_of_day=True)[0] + 1))
    if include_closed:
        params.append(("include_closed", "true"))
    if subtasks:
        params.append(("subtasks", "true"))
    return params, scope


def _is_last_page(data: dict, tasks: list) -> bool:
    return bool(data.get("last_page", len(tasks) < _PAGE_SIZE))


def _page_of_tasks(c: ClickUpClient, account: str, params: list, scope: str, page: int,
                   limit: int) -> dict:
    data = c.request(account, "GET", f"/team/{c.config.workspace_id}/task", params=params + [("page", page)])
    tasks = data.get("tasks") or []
    shown = tasks[:limit]
    result: dict[str, Any] = {
        "account": account, "scope": scope, "page": page, "count": len(shown),
        "has_more": not _is_last_page(data, tasks),
        "tasks": [c.shape_task(t) for t in shown],
    }
    if len(tasks) > limit:
        # `page` advances 100 tasks at a time (ClickUp's page): cutting with `limit` and
        # asking for the next page would skip rows, so say so instead of hiding it.
        result["truncated"] = True
        result["warning"] = (f"Showing {limit} of {len(tasks)} tasks on this page; raise `limit` "
                             "(max 100) or narrow the filters before paging.")
    return result


def _scan_by_text(c: ClickUpClient, account: str, params: list, scope: str, page: int,
                  limit: int, text: str) -> dict:
    # ClickUp has no server-side text search: filter over the pages read.
    needle = _norm(text)
    matches: list[dict] = []
    scanned, exhausted = 0, False
    for p in range(page, page + _MAX_SCAN_PAGES):
        data = c.request(account, "GET", f"/team/{c.config.workspace_id}/task", params=params + [("page", p)])
        tasks = data.get("tasks") or []
        scanned += len(tasks)
        matches.extend(t for t in tasks if needle in _norm(t.get("name", "")))
        if _is_last_page(data, tasks) or not tasks:
            exhausted = True
            break
        if len(matches) >= limit:
            break
    result: dict[str, Any] = {
        "account": account, "scope": scope, "text": text, "scanned": scanned,
        "complete": exhausted, "count": min(len(matches), limit),
        "tasks": [c.shape_task(t) for t in matches[:limit]],
    }
    if not exhausted:
        result["warning"] = (f"Incomplete text search: only the first {scanned} tasks were checked. "
                             "Narrow with list/space/statuses/assignees.")
    return result


def find_tasks(c: ClickUpClient, account: str, *, list_ref: str = "", space: str = "",
               statuses: list[str] | None = None, assignees: list[str] | None = None,
               tags: list[str] | None = None, due_from: str = "", due_to: str = "",
               overdue: bool = False, text: str = "", include_closed: bool = False,
               subtasks: bool = False, order_by: str = "updated", page: int = 0,
               limit: int = 50) -> dict:
    if order_by not in _ORDER_BY:
        raise ValueError(f"order_by must be one of: {', '.join(_ORDER_BY)}")
    limit = max(1, min(int(limit), _PAGE_SIZE))
    params, scope = _find_params(
        c, account, list_ref=list_ref, space=space, statuses=statuses, assignees=assignees,
        tags=tags, due_from=due_from, due_to=due_to, overdue=overdue,
        include_closed=include_closed, subtasks=subtasks, order_by=order_by,
    )
    if _norm(text):
        return _scan_by_text(c, account, params, scope, int(page), limit, text)
    return _page_of_tasks(c, account, params, scope, int(page), limit)


# --------------------------------------------------------------------------- tasks


def get_task(c: ClickUpClient, account: str, task_id: str, *, include_subtasks: bool = True,
             include_comments: bool = False, custom_task_id: bool = False) -> dict:
    task_id = _task_id(task_id)
    params: dict[str, Any] = {"include_markdown_description": "true"}
    if include_subtasks:
        params["include_subtasks"] = "true"
    if custom_task_id:
        params.update(custom_task_ids="true", team_id=c.config.workspace_id)
    raw = c.request(account, "GET", f"/task/{task_id}", params=params)
    result = {"account": account, "task": c.shape_task(raw, full=True)}
    if include_comments:
        result["comments"] = list_comments(c, account, raw.get("id") or task_id)["comments"]
    return result


def create_task(c: ClickUpClient, account: str, *, list_ref: str, name: str, description: str = "",
                status: str = "", assignees: list[str] | None = None, due_date: str = "",
                start_date: str = "", priority: str = "", tags: list[str] | None = None,
                parent: str = "", custom_fields: dict | None = None) -> dict:
    if not (name or "").strip():
        raise ValueError("The task needs a name")
    target = c.resolve_list(account, list_ref)
    body: dict[str, Any] = {"name": name.strip()}
    if description:
        body["description"] = description
        body["markdown_content"] = description
    if status:
        body["status"] = _match_status(status, c.list_statuses(account, target["id"]))
    if assignees:
        body["assignees"] = [c.resolve_member(account, a) for a in assignees]
    if due_date:
        body["due_date"], body["due_date_time"] = c.parse_date(due_date)
    if start_date:
        body["start_date"], body["start_date_time"] = c.parse_date(start_date)
    if priority:
        body["priority"] = _priority_value(priority)
    if tags:
        body["tags"] = tags
    if parent:
        body["parent"] = _task_id(parent)
    if custom_fields:
        body["custom_fields"] = c._custom_field_payload(account, target["id"], custom_fields)
    raw = c.request(account, "POST", f"/list/{target['id']}/task", json=body)
    return {"account": account, "created": True, "list": target["path"], "task": c.shape_task(raw)}


def _update_body(c: ClickUpClient, account: str, *, name: str | None, description: str | None,
                 priority: str | None, due_date: str | None, start_date: str | None,
                 add_assignees: list[str] | None, remove_assignees: list[str] | None,
                 archived: bool | None) -> dict:
    body: dict[str, Any] = {}
    if name is not None:
        if not name.strip():
            raise ValueError("The name cannot be empty")
        body["name"] = name.strip()
    if description is not None:
        body["description"] = description
        body["markdown_content"] = description
    if priority is not None:
        body["priority"] = _priority_value(priority)
    for key, value in (("due_date", due_date), ("start_date", start_date)):
        if value is None:
            continue
        if value.strip().lower() in _CLEAR:
            body[key] = None
        else:
            body[key], body[f"{key}_time"] = c.parse_date(value)
    if add_assignees or remove_assignees:
        body["assignees"] = {"add": [c.resolve_member(account, a) for a in add_assignees or []],
                             "rem": [c.resolve_member(account, a) for a in remove_assignees or []]}
    if archived is not None:
        body["archived"] = bool(archived)
    return body


def update_task(c: ClickUpClient, account: str, task_id: str, *, name: str | None = None,
                description: str | None = None, status: str | None = None,
                priority: str | None = None, due_date: str | None = None,
                start_date: str | None = None, add_assignees: list[str] | None = None,
                remove_assignees: list[str] | None = None, add_tags: list[str] | None = None,
                remove_tags: list[str] | None = None, move_to_list: str | None = None,
                archived: bool | None = None) -> dict:
    task_id = _task_id(task_id)
    body = _update_body(c, account, name=name, description=description, priority=priority,
                        due_date=due_date, start_date=start_date, add_assignees=add_assignees,
                        remove_assignees=remove_assignees, archived=archived)
    destination = c.resolve_list(account, move_to_list) if move_to_list else None
    if not (body or status or add_tags or remove_tags or destination):
        raise ValueError("No changes: give at least one field to update")

    if destination:
        # Moving changes the status set; the status is validated against the destination after.
        c.request(account, "PUT",
                  f"/workspaces/{c.config.workspace_id}/tasks/{task_id}/home_list/{destination['id']}",
                  json={}, v3=True)
    if status is not None:
        current = c.request(account, "GET", f"/task/{task_id}")
        list_id = (current.get("list") or {}).get("id")
        body["status"] = _match_status(status, c.list_statuses(account, list_id)) if list_id else status
    if body:
        c.request(account, "PUT", f"/task/{task_id}", json=body)
    for tag in add_tags or []:
        c.request(account, "POST", f"/task/{task_id}/tag/{quote(tag, safe='')}", json={})
    for tag in remove_tags or []:
        c.request(account, "DELETE", f"/task/{task_id}/tag/{quote(tag, safe='')}")
    fresh = c.request(account, "GET", f"/task/{task_id}")
    if destination and str((fresh.get("list") or {}).get("id")) != destination["id"]:
        raise ClickUpError(f"Task {task_id} did NOT end up in {destination['path']}; the other "
                           "changes were applied. Move it from the ClickUp UI.")
    return {"account": account, "updated": True, "task": c.shape_task(fresh)}


def set_custom_field(c: ClickUpClient, account: str, task_id: str, field: str, value: Any) -> dict:
    task_id = _task_id(task_id)
    task = c.request(account, "GET", f"/task/{task_id}")
    target = _match_field(task.get("custom_fields") or [], field)
    if value is None:
        c.request(account, "DELETE", f"/task/{task_id}/field/{target['id']}")
    else:
        c.request(account, "POST", f"/task/{task_id}/field/{target['id']}",
                  json={"value": c._field_value(target, value)})
    return {"account": account, "updated": True, "task_id": task_id, "field": target.get("name"),
            "value": value}


# --------------------------------------------------------------------------- comments


def list_comments(c: ClickUpClient, account: str, task_id: str, limit: int = 25) -> dict:
    task_id = _task_id(task_id)
    raw = c.request(account, "GET", f"/task/{task_id}/comment").get("comments") or []
    limit = max(1, min(int(limit), _PAGE_SIZE))
    comments = [{
        "id": item.get("id"),
        "author": (item.get("user") or {}).get("username") or (item.get("user") or {}).get("email"),
        "date": c._ms_to_iso(item.get("date")),
        "text": (item.get("comment_text") or "")[:_MAX_COMMENT_CHARS],
        "resolved": bool(item.get("resolved")),
    } for item in raw[:limit]]
    return {"account": account, "task_id": task_id, "count": len(comments), "total": len(raw),
            "comments": comments}


def add_comment(c: ClickUpClient, account: str, task_id: str, text: str, *,
                notify_all: bool = False, assignee: str = "") -> dict:
    task_id = _task_id(task_id)
    if not (text or "").strip():
        raise ValueError("The comment is empty")
    body: dict[str, Any] = {"comment_text": text.strip(), "notify_all": bool(notify_all)}
    if assignee:
        body["assignee"] = c.resolve_member(account, assignee)
    raw = c.request(account, "POST", f"/task/{task_id}/comment", json=body)
    return {"account": account, "created": True, "task_id": task_id, "comment_id": raw.get("id")}


# --------------------------------------------------------------------------- checklists


def _pick_checklist(lists: list[dict], ref: str) -> dict:
    if not lists:
        raise ValueError("The task has no checklists; create one with action='add'")
    if ref:
        hits = [cl for cl in lists if cl.get("id") == ref or _norm(cl.get("name", "")) == _norm(ref)]
    else:
        hits = lists if len(lists) == 1 else []
    if len(hits) != 1:
        raise ValueError("Give `checklist` (name or ID): " + "; ".join(cl.get("name", "") for cl in lists))
    return hits[0]


def _pick_item(target: dict, ref: str) -> dict:
    items = target.get("items") or []
    found = [i for i in items if i.get("id") == ref] or [i for i in items if _norm(i.get("name", "")) == _norm(ref)]
    if len(found) != 1:
        raise ValueError("Give `item` (exact name or ID): " + "; ".join(i.get("name", "") for i in items))
    return found[0]


def checklist(c: ClickUpClient, account: str, task_id: str, action: str, *, checklist: str = "",
              item: str = "", name: str = "") -> dict:
    task_id = _task_id(task_id)
    if action not in _CHECKLIST_ACTIONS:
        raise ValueError(f"Invalid action {action!r}: {', '.join(_CHECKLIST_ACTIONS)}")
    lists = c.request(account, "GET", f"/task/{task_id}").get("checklists") or []
    if action == "list":
        return {"account": account, "task_id": task_id, "checklists": _shape_checklists(lists)}
    if action == "add":
        if not name.strip():
            raise ValueError("Give `name` for the new checklist")
        raw = c.request(account, "POST", f"/task/{task_id}/checklist", json={"name": name.strip()})
        return {"account": account, "created": True, "checklist": (raw.get("checklist") or {}).get("id")}

    target = _pick_checklist(lists, checklist)
    if action == "add_item":
        if not name.strip():
            raise ValueError("Give `name` for the new item")
        c.request(account, "POST", f"/checklist/{target['id']}/checklist_item", json={"name": name.strip()})
        return {"account": account, "created": True, "checklist": target.get("name"), "item": name.strip()}

    entry = _pick_item(target, item)
    path = f"/checklist/{target['id']}/checklist_item/{entry['id']}"
    if action == "remove_item":
        c.request(account, "DELETE", path)
    elif action == "rename_item":
        if not name.strip():
            raise ValueError("Give `name` with the item's new text")
        c.request(account, "PUT", path, json={"name": name.strip()})
    else:
        c.request(account, "PUT", path, json={"resolved": action == "check"})
    return {"account": account, "updated": True, "action": action, "checklist": target.get("name"),
            "item": entry.get("name")}
