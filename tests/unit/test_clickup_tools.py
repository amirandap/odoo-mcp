"""ClickUp MCP surface: tool definitions, executor and the CRUD-admin access gate."""
import importlib
import json

import httpx
import pytest
from fastapi import HTTPException

from odoo_mcp_server.clickup import ClickUpClient, ClickUpConfig
from odoo_mcp_server.config import TOOL_SCOPE_REQUIREMENTS, WRITE_TOOLS
from odoo_mcp_server.tools import CLICKUP_TOOLS, execute_tool, register_tools, tool_group_for
from odoo_mcp_server.tools.clickup import execute_clickup_tool

pytestmark = pytest.mark.unit

CLICKUP_TOOL_NAMES = {
    "clickup_whoami", "clickup_hierarchy", "clickup_find_tasks", "clickup_get_task",
    "clickup_create_task", "clickup_update_task", "clickup_set_custom_field",
    "clickup_list_comments", "clickup_add_comment", "clickup_checklist",
}
WRITES = {"clickup_create_task", "clickup_update_task", "clickup_set_custom_field",
          "clickup_add_comment", "clickup_checklist"}


def _client(handler):
    config = ClickUpConfig(accounts={"ops": "pk_ops", "sales": "pk_sales"}, workspace_id="1")
    return ClickUpClient(config, transport=httpx.MockTransport(handler))


def _payload(result):
    return json.loads(result[0].text)


class TestSurface:
    def test_group_exposes_exactly_the_ported_tools(self):
        assert {t.name for t in CLICKUP_TOOLS} == CLICKUP_TOOL_NAMES
        assert all(tool_group_for(name) == "clickup" for name in CLICKUP_TOOL_NAMES)

    def test_group_is_opt_in(self):
        assert not CLICKUP_TOOL_NAMES & {t.name for t in register_tools()}
        assert CLICKUP_TOOL_NAMES <= {t.name for t in register_tools({"crud", "clickup"})}

    def test_every_tool_has_optional_account_and_description(self):
        for tool in CLICKUP_TOOLS:
            assert tool.description
            assert "account" in tool.inputSchema["properties"]
            assert "account" not in tool.inputSchema.get("required", [])

    def test_reads_need_odoo_read_and_writes_need_odoo_write(self):
        for name in CLICKUP_TOOL_NAMES:
            expected = ["odoo.write"] if name in WRITES else ["odoo.read"]
            assert TOOL_SCOPE_REQUIREMENTS[name] == expected
        assert WRITES <= set(WRITE_TOOLS)


class TestExecutor:
    async def test_runs_with_the_requested_account(self):
        tokens = []

        def handler(request):
            tokens.append(request.headers["Authorization"])
            return httpx.Response(200, json={"id": "abc", "name": "T"})

        result = await execute_clickup_tool("clickup_get_task", {"task_id": "abc", "account": "sales"}, _client(handler))
        assert _payload(result)["account"] == "sales"
        assert tokens == ["pk_sales"]

    async def test_actionable_errors_are_returned_not_raised(self):
        def handler(request):
            raise AssertionError("must not reach ClickUp")

        client = _client(handler)
        bad_account = await execute_clickup_tool("clickup_get_task", {"task_id": "abc", "account": "x"}, client)
        assert "Unknown ClickUp account" in _payload(bad_account)["error"]
        missing = await execute_clickup_tool("clickup_get_task", {}, client)
        assert "Missing argument" in _payload(missing)["error"]

    async def test_clickup_api_errors_never_leak_the_token(self):
        def handler(request):
            return httpx.Response(401, json={"err": "Token invalid", "ECODE": "OAUTH_025"})

        result = await execute_clickup_tool("clickup_get_task", {"task_id": "abc"}, _client(handler))
        error = _payload(result)["error"]
        assert "401" in error and "pk_ops" not in error

    async def test_unconfigured_server_raises(self):
        with pytest.raises(ValueError, match="not configured"):
            await execute_clickup_tool("clickup_whoami", {}, None)

    async def test_execute_tool_routes_clickup_without_touching_odoo(self):
        def handler(request):
            return httpx.Response(200, json={"id": "abc", "name": "T"})

        result = await execute_tool("clickup_get_task", {"task_id": "abc"}, odoo_client=None,
                                    clickup_client=_client(handler))
        assert _payload(result)["task"]["id"] == "abc"


@pytest.fixture
def http_server_module(monkeypatch):
    monkeypatch.setenv("ODOO_URL", "https://odoo.example.test")
    monkeypatch.setenv("ODOO_DB", "test_db")
    monkeypatch.setenv("ODOO_API_KEY", "shared-service-key")
    monkeypatch.setenv("ODOO_USERNAME", "svc@example.test")
    monkeypatch.setenv("OAUTH_DEV_MODE", "true")
    monkeypatch.setenv("ENABLED_TOOL_GROUPS", "crud,employee,clickup")
    monkeypatch.setenv("CLICKUP_ACCOUNTS", '{"ops": "pk_ops"}')
    monkeypatch.setenv("CLICKUP_WORKSPACE_ID", "1")
    monkeypatch.delenv("USER_KEY_VAULT_PATH", raising=False)
    monkeypatch.delenv("USER_KEY_VAULT_ENCRYPTION_KEY", raising=False)

    import odoo_mcp_server.http_server as module

    importlib.reload(module)
    return module


class TestAccessGate:
    async def test_admin_sees_clickup_tools(self, http_server_module):
        result = await http_server_module.handle_tools_list({"email": "a@x.com", "scopes": ["odoo.read", "odoo.write"]})
        assert CLICKUP_TOOL_NAMES <= {t["name"] for t in result["tools"]}

    async def test_employee_does_not_see_clickup_tools(self, http_server_module):
        result = await http_server_module.handle_tools_list({"email": "e@x.com", "scopes": ["odoo.hr.profile"]})
        assert not CLICKUP_TOOL_NAMES & {t["name"] for t in result["tools"]}

    async def test_vault_linked_user_gets_no_clickup_bypass(self, http_server_module, monkeypatch):
        class Vault:
            def get_credentials(self, email):
                return ("me@x.com", "personal-odoo-key")

        monkeypatch.setattr(http_server_module, "user_vault", Vault())
        user = {"email": "e@x.com", "scopes": ["odoo.hr.profile"]}
        listed = {t["name"] for t in (await http_server_module.handle_tools_list(user))["tools"]}
        assert "search_records" in listed
        assert not CLICKUP_TOOL_NAMES & listed
        with pytest.raises(HTTPException) as exc:
            await http_server_module.handle_tools_call({"name": "clickup_whoami", "arguments": {}}, user)
        assert exc.value.status_code == 403

    async def test_read_only_admin_cannot_write(self, http_server_module):
        user = {"email": "a@x.com", "scopes": ["odoo.read"]}
        with pytest.raises(HTTPException) as exc:
            await http_server_module.handle_tools_call(
                {"name": "clickup_create_task", "arguments": {"list": "1", "name": "x"}}, user)
        assert exc.value.status_code == 403

    async def test_lifespan_builds_client_and_fails_fast_on_bad_config(self, http_server_module, monkeypatch):
        from odoo_mcp_server.clickup import build_clickup_client

        assert build_clickup_client(http_server_module.settings) is not None
        monkeypatch.setattr(http_server_module.settings, "clickup_workspace_id", None)
        with pytest.raises(ValueError, match="CLICKUP_WORKSPACE_ID"):
            build_clickup_client(http_server_module.settings)
