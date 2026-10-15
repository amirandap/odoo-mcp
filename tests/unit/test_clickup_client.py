"""Native ClickUp client: accounts, identity pin, errors, resolution, task mapping.

Ported from cos-mcp-public's test_clickup_native.py. HTTP goes through an
``httpx.MockTransport`` router of canned responses, so nothing reaches api.clickup.com.
"""
import json as jsonlib
from urllib.parse import unquote

import httpx
import pytest

from odoo_mcp_server.clickup import ClickUpClient, ClickUpConfig, ClickUpError
from odoo_mcp_server.clickup import client as client_module
from odoo_mcp_server.clickup import operations as ops

pytestmark = pytest.mark.unit

OPS = "pk_test_ops"
SALES = "pk_test_sales"
SALES_USER_ID = 4242
WS = "90100000000"


class FakeClickUp:
    """Routes (method, path) to responses and records every call."""

    def __init__(self):
        self.routes = {}
        self.calls = []

    def on(self, method, path, body=None, status=200, headers=None):
        self.routes.setdefault((method, path), []).append((status, body if body is not None else {}, headers or {}))

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url.copy_with(query=None))
        path = url.split("/api/v2", 1)[-1] if "/api/v2" in url else "v3:" + url.split("/api/v3", 1)[-1]
        path = unquote(path).replace(" ", "%20")
        body = jsonlib.loads(request.content) if request.content else None
        self.calls.append({"method": request.method, "path": path, "params": list(request.url.params.multi_items()),
                           "json": body, "token": request.headers["Authorization"]})
        queue = self.routes.get((request.method, path))
        if not queue:
            raise AssertionError(f"unexpected request {request.method} {path}")
        status, payload, headers = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(status, json=payload, headers=headers)

    def paths(self):
        return [(c["method"], c["path"]) for c in self.calls]

    def params(self, call):
        return dict(call["params"])


HIERARCHY = {
    ("GET", f"/team/{WS}/space"): {"spaces": [{"id": 1, "name": "Acme"}, {"id": 2, "name": "Personal"}]},
    ("GET", "/space/1/folder"): {"folders": [{"id": 10, "name": "Sales", "lists": [
        {"id": 100, "name": "Opportunities"}, {"id": 101, "name": "Clients"}]}]},
    ("GET", "/space/1/list"): {"lists": [{"id": 102, "name": "Inbox"}]},
    ("GET", "/space/2/folder"): {"folders": []},
    ("GET", "/space/2/list"): {"lists": [{"id": 200, "name": "Inbox"}]},
}


def make_client(fake, **overrides):
    config = {
        "accounts": {"ops": OPS, "sales": SALES},
        "workspace_id": WS,
        "expected_user_ids": {"sales": SALES_USER_ID},
        "timezone": "America/Santo_Domingo",
        **overrides,
    }
    return ClickUpClient(ClickUpConfig(**config), transport=httpx.MockTransport(fake.handler))


@pytest.fixture
def api(monkeypatch):
    fake = FakeClickUp()
    monkeypatch.setattr(client_module.time, "sleep", lambda s: None)
    for (method, path), body in HIERARCHY.items():
        fake.on(method, path, body)
    return fake


@pytest.fixture
def cu(api):
    client = make_client(api)
    yield client
    client.close()


def sales_ok(api):
    api.on("GET", "/user", {"user": {"id": SALES_USER_ID, "username": "Sales"}})


# ------------------------------------------------------------------ config


class TestConfig:
    def test_identical_tokens_are_rejected(self):
        with pytest.raises(ValueError, match="distinct"):
            ClickUpConfig(accounts={"a": OPS, "b": OPS}, workspace_id=WS)

    @pytest.mark.parametrize("kwargs, match", [
        ({"accounts": {}}, "at least one"),
        ({"workspace_id": ""}, "CLICKUP_WORKSPACE_ID"),
        ({"accounts": {"a": " "}}, "non-empty"),
        ({"expected_user_ids": {"ghost": 1}}, "unknown accounts"),
        ({"default_account": "ghost"}, "CLICKUP_DEFAULT_ACCOUNT"),
    ])
    def test_invalid_config_fails_fast(self, kwargs, match):
        base = {"accounts": {"ops": OPS}, "workspace_id": WS}
        with pytest.raises(ValueError, match=match):
            ClickUpConfig(**{**base, **kwargs})

    def test_default_account_is_first_unless_configured(self):
        assert ClickUpConfig(accounts={"ops": OPS, "sales": SALES}, workspace_id=WS).effective_default_account == "ops"
        cfg = ClickUpConfig(accounts={"ops": OPS, "sales": SALES}, workspace_id=WS, default_account="sales")
        assert cfg.effective_default_account == "sales"


# ------------------------------------------------------------------ accounts


class TestAccounts:
    def test_default_account_uses_its_own_token(self, api, cu):
        api.on("GET", "/task/abc", {"id": "abc", "name": "T"})
        result = ops.get_task(cu, cu.check_account(None), "abc", include_subtasks=False)
        assert result["account"] == "ops"
        assert {c["token"] for c in api.calls} == {OPS}

    def test_pinned_account_is_verified_once_and_uses_its_token(self, api, cu):
        sales_ok(api)
        api.on("GET", "/task/abc", {"id": "abc", "name": "T"})
        ops.get_task(cu, "sales", "abc")
        ops.get_task(cu, "sales", "abc")
        assert api.paths().count(("GET", "/user")) == 1
        assert {c["token"] for c in api.calls} == {SALES}

    def test_wrong_identity_is_rejected_and_not_cached(self, api, cu):
        api.on("GET", "/user", {"user": {"id": 123}})
        for _ in range(2):
            with pytest.raises(ClickUpError, match="expected user"):
                ops.get_task(cu, "sales", "abc")
        assert api.paths() == [("GET", "/user"), ("GET", "/user")]

    @pytest.mark.parametrize("bad", ["other", 0, "Sales"])
    def test_unknown_account_fails_closed(self, api, cu, bad):
        with pytest.raises(ValueError, match="Unknown ClickUp account"):
            cu.check_account(bad)
        assert api.calls == []

    def test_failure_never_falls_back_to_another_account(self, api, cu):
        sales_ok(api)
        api.on("PUT", "/task/abc", {"err": "nope", "ECODE": "ACCESS_001"}, status=403)
        with pytest.raises(ClickUpError, match="'sales' is not allowed"):
            ops.update_task(cu, "sales", "abc", name="New")
        assert {c["token"] for c in api.calls} == {SALES}


# ------------------------------------------------------------------ errors


class TestErrors:
    def test_401_mentions_account_and_never_the_token(self, api, cu):
        api.on("GET", "/task/abc", {"err": "Token invalid", "ECODE": "OAUTH_025"}, status=401)
        with pytest.raises(ClickUpError) as exc:
            ops.get_task(cu, "ops", "abc")
        assert exc.value.status == 401
        assert "CLICKUP_ACCOUNTS" in str(exc.value)
        assert OPS not in str(exc.value)

    def test_404_and_500_are_mapped(self, api, cu):
        api.on("GET", "/task/abc", {"err": "Task not found", "ECODE": "ITEM_013"}, status=404)
        with pytest.raises(ClickUpError, match=r"404.*Task not found \(ITEM_013\)"):
            ops.get_task(cu, "ops", "abc")
        api.on("GET", f"/team/{WS}/task", {"err": "boom"}, status=500)
        with pytest.raises(ClickUpError, match="filters"):
            ops.find_tasks(cu, "ops")

    def test_read_429_is_retried_once_but_write_429_is_not(self, api, cu):
        api.on("GET", "/task/abc", {}, status=429)
        api.on("GET", "/task/abc", {"id": "abc", "name": "T"})
        assert ops.get_task(cu, "ops", "abc", include_subtasks=False)["task"]["id"] == "abc"
        api.on("POST", "/task/abc/comment", {}, status=429)
        with pytest.raises(ClickUpError, match="429"):
            ops.add_comment(cu, "ops", "abc", "hello")
        assert api.paths().count(("POST", "/task/abc/comment")) == 1

    def test_transport_error_is_wrapped_without_details(self):
        def boom(request):
            raise httpx.ConnectError(f"failed with header {OPS}")

        client = ClickUpClient(ClickUpConfig(accounts={"ops": OPS}, workspace_id=WS),
                               transport=httpx.MockTransport(boom))
        with pytest.raises(ClickUpError) as exc:
            ops.get_task(client, "ops", "abc")
        assert OPS not in str(exc.value)
        assert "ConnectError" in str(exc.value)

    def test_invalid_task_id_never_reaches_the_api(self, api, cu):
        with pytest.raises(ValueError, match="Invalid task ID"):
            ops.get_task(cu, "ops", "../team")
        assert api.calls == []


# ------------------------------------------------------------------ resolution


class TestResolution:
    def test_resolve_list_by_path_name_and_ambiguity(self, cu):
        assert cu.resolve_list("ops", "Acme/Sales/Opportunities")["id"] == "100"
        assert cu.resolve_list("ops", "acme/opportunities")["id"] == "100"
        assert cu.resolve_list("ops", "Clients")["id"] == "101"
        assert cu.resolve_list("ops", "Personal/Inbox")["id"] == "200"
        with pytest.raises(ValueError, match="ambiguous"):
            cu.resolve_list("ops", "Inbox")
        with pytest.raises(ValueError, match="No list"):
            cu.resolve_list("ops", "Nothing")

    def test_hierarchy_is_cached_per_account(self, api, cu):
        cu.hierarchy("ops")
        cu.hierarchy("ops")
        assert api.paths().count(("GET", f"/team/{WS}/space")) == 1

    def test_member_resolution(self, api, cu):
        api.on("GET", "/team", {"teams": [{"id": WS, "members": [
            {"user": {"id": 1, "username": "Ana Ops", "email": "a@x.com"}},
            {"user": {"id": 2, "username": "Pat Sales", "email": "p@x.com"}},
        ]}]})
        api.on("GET", "/user", {"user": {"id": 1, "username": "Ana Ops"}})
        assert cu.resolve_member("ops", "p@x.com") == 2
        assert cu.resolve_member("ops", "pat") == 2
        assert cu.resolve_member("ops", "me") == 1
        assert cu.resolve_member("ops", "42") == 42
        with pytest.raises(ValueError, match="No person"):
            cu.resolve_member("ops", "zzz")


# ------------------------------------------------------------------ tasks


class TestTasks:
    def test_find_tasks_builds_filters_and_reports_paging(self, api, cu):
        api.on("GET", "/list/100", {"statuses": [{"status": "to do"}, {"status": "in progress"}]})
        api.on("GET", f"/team/{WS}/task", {"tasks": [
            {"id": "t1", "name": "One", "status": {"status": "in progress"}, "priority": {"priority": "high"},
             "assignees": [{"username": "Ana"}], "due_date": "1792022400000",
             "list": {"id": "100", "name": "Opportunities"}},
        ], "last_page": False})
        result = ops.find_tasks(cu, "ops", list_ref="Sales/Opportunities", statuses=["IN PROGRESS"],
                                tags=["vip"], due_from="2026-10-01", due_to="2026-10-31", page=2)
        call = api.calls[-1]
        params = api.params(call)
        assert ("list_ids[]", "100") in call["params"]
        assert ("statuses[]", "in progress") in call["params"]
        assert ("tags[]", "vip") in call["params"]
        assert params["page"] == "2" and params["order_by"] == "updated"
        assert int(params["due_date_gt"]) < int(params["due_date_lt"])
        assert result["has_more"] is True and "truncated" not in result
        assert result["tasks"][0] == {
            "id": "t1", "name": "One", "status": "in progress", "priority": "high", "assignees": ["Ana"],
            "due_date": result["tasks"][0]["due_date"], "list": "Opportunities", "list_id": "100",
        }

    def test_find_tasks_rejects_unknown_status_with_options(self, api, cu):
        api.on("GET", "/list/100", {"statuses": [{"status": "to do"}]})
        with pytest.raises(ValueError, match="Valid: to do"):
            ops.find_tasks(cu, "ops", list_ref="100", statuses=["done"])

    def test_find_tasks_text_scan_reports_completeness(self, api, cu):
        api.on("GET", f"/team/{WS}/task", {"tasks": [{"id": "a", "name": "Quote Vivaldi"},
                                                    {"id": "b", "name": "Something else"}], "last_page": True})
        result = ops.find_tasks(cu, "ops", text="vivaldi")
        assert result["complete"] is True and result["count"] == 1
        assert result["tasks"][0]["id"] == "a"

    def test_find_tasks_rejects_priority_ordering(self, cu):
        with pytest.raises(ValueError, match="order_by"):
            ops.find_tasks(cu, "ops", order_by="priority")

    def test_find_tasks_flags_a_page_cut_by_limit(self, api, cu):
        api.on("GET", f"/team/{WS}/task", {"tasks": [{"id": str(i), "name": "x"} for i in range(5)],
                                          "last_page": True})
        result = ops.find_tasks(cu, "ops", limit=2)
        assert result["count"] == 2 and result["has_more"] is False
        assert result["truncated"] is True and "limit" in result["warning"]

    def test_get_task_shapes_full_detail(self, api, cu):
        api.on("GET", "/task/abc", {
            "id": "abc", "name": "T", "markdown_description": "**hi**",
            "custom_fields": [
                {"id": "f1", "name": "Stage", "type": "drop_down", "value": 1,
                 "type_config": {"options": [{"id": "o0", "name": "Lead", "orderindex": 0},
                                             {"id": "o1", "name": "Won", "orderindex": 1}]}},
                {"id": "f2", "name": "Empty", "type": "text"},
            ],
            "checklists": [{"id": "c1", "name": "Steps", "items": [{"id": "i1", "name": "Call", "resolved": True}]}],
            "subtasks": [{"id": "s1", "name": "Sub"}],
        })
        task = ops.get_task(cu, "ops", "abc")["task"]
        assert api.params(api.calls[0])["include_subtasks"] == "true"
        assert task["description"] == "**hi**"
        assert task["custom_fields"] == [{"id": "f1", "name": "Stage", "type": "drop_down", "value": "Won"}]
        assert task["checklists"][0]["items"][0] == {"id": "i1", "name": "Call", "resolved": True}
        assert task["subtasks"] == [{"id": "s1", "name": "Sub"}]

    def test_create_task_maps_fields(self, api, cu):
        api.on("GET", "/list/100", {"statuses": [{"status": "to do"}]})
        api.on("GET", "/team", {"teams": [{"id": WS, "members": [{"user": {"id": 7, "username": "Pat", "email": "p@x.com"}}]}]})
        api.on("GET", "/list/100/field", {"fields": [{"id": "f1", "name": "Stage", "type": "drop_down",
                                                     "type_config": {"options": [{"id": "o1", "name": "Lead"}]}}]})
        api.on("POST", "/list/100/task", {"id": "new", "name": "New", "url": "https://app.clickup.com/t/new"})
        result = ops.create_task(cu, "ops", list_ref="Opportunities", name=" New ", description="# md",
                                 status="TO DO", assignees=["p@x.com"], due_date="2026-10-20",
                                 priority="high", tags=["vip"], custom_fields={"Stage": "lead"})
        body = api.calls[-1]["json"]
        assert body["name"] == "New" and body["markdown_content"] == "# md"
        assert body["status"] == "to do" and body["assignees"] == [7]
        assert body["priority"] == 2 and body["due_date_time"] is False
        assert body["custom_fields"] == [{"id": "f1", "value": "o1"}]
        assert result["created"] is True and result["task"]["id"] == "new"

    def test_update_task_status_assignees_tags_and_clear_due(self, api, cu):
        api.on("GET", "/task/abc", {"id": "abc", "name": "T", "list": {"id": "100"}})
        api.on("GET", "/list/100", {"statuses": [{"status": "complete"}]})
        api.on("PUT", "/task/abc", {"id": "abc"})
        api.on("POST", "/task/abc/tag/a%20b", {})
        result = ops.update_task(cu, "ops", "abc", status="Complete", due_date="none",
                                 priority="none", add_assignees=["5"], remove_assignees=[6],
                                 add_tags=["a b"])
        put = next(c for c in api.calls if c["method"] == "PUT")["json"]
        assert put == {"status": "complete", "due_date": None, "priority": None,
                       "assignees": {"add": [5], "rem": [6]}}
        assert result["updated"] is True

    def test_update_task_requires_a_change(self, api, cu):
        with pytest.raises(ValueError, match="No changes"):
            ops.update_task(cu, "ops", "abc")
        assert api.calls == []

    def test_move_uses_v3_and_fails_loudly_if_not_moved(self, api, cu):
        api.on("PUT", f"v3:/workspaces/{WS}/tasks/abc/home_list/101", {})
        api.on("GET", "/task/abc", {"id": "abc", "list": {"id": "100"}})
        with pytest.raises(ClickUpError, match="did NOT end up"):
            ops.update_task(cu, "ops", "abc", move_to_list="Clients")
        assert ("PUT", f"v3:/workspaces/{WS}/tasks/abc/home_list/101") in api.paths()

    def test_set_custom_field_and_clear(self, api, cu):
        api.on("GET", "/task/abc", {"id": "abc", "custom_fields": [
            {"id": "f1", "name": "Stage", "type": "drop_down",
             "type_config": {"options": [{"id": "o1", "name": "Lead"}]}}]})
        api.on("POST", "/task/abc/field/f1", {})
        api.on("DELETE", "/task/abc/field/f1", {})
        assert ops.set_custom_field(cu, "ops", "abc", "stage", "Lead")["field"] == "Stage"
        assert api.calls[-1]["json"] == {"value": "o1"}
        ops.set_custom_field(cu, "ops", "abc", "Stage", None)
        assert api.calls[-1]["method"] == "DELETE"

    def test_comments_and_checklist(self, api, cu):
        api.on("GET", "/task/abc/comment", {"comments": [{"id": "c", "comment_text": "hi",
                                                          "user": {"username": "Ana"}, "date": "1792022400000"}]})
        assert ops.list_comments(cu, "ops", "abc")["comments"][0]["text"] == "hi"
        api.on("POST", "/task/abc/comment", {"id": 99})
        assert ops.add_comment(cu, "ops", "abc", " ok ")["comment_id"] == 99
        assert api.calls[-1]["json"] == {"comment_text": "ok", "notify_all": False}

        api.on("GET", "/task/abc", {"id": "abc", "checklists": [
            {"id": "cl", "name": "Steps", "items": [{"id": "it", "name": "Call"}]}]})
        api.on("PUT", "/checklist/cl/checklist_item/it", {})
        assert ops.checklist(cu, "ops", "abc", "check", item="call")["updated"] is True
        assert api.calls[-1]["json"] == {"resolved": True}
        with pytest.raises(ValueError, match="Give `item`"):
            ops.checklist(cu, "ops", "abc", "check", item="nothing")


def test_parse_date_variants(cu):
    ms, has_time = cu.parse_date("2026-10-20")
    assert has_time is False and ms > 0
    assert cu.parse_date("2026-10-20T15:30")[1] is True
    with pytest.raises(ValueError, match="Invalid date"):
        cu.parse_date("next monday")
