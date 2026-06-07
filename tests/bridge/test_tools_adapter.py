"""Unit tests for the per-user tool dispatch adapter."""

import pytest

pytest.importorskip("fastmcp")  # adapter imports fastmcp; skip if the [bridge] extra is absent

from mcp.types import TextContent  # noqa: E402

from odoo_mcp_bridge import tools_adapter  # noqa: E402

pytestmark = [pytest.mark.unit]


class FakeUserClient:
    def __init__(self, uid=99, employees=None):
        self._uid = uid
        self._employees = [{"id": 4265}] if employees is None else employees
        self.closed = False

    async def authenticate(self):
        return self._uid

    async def execute(self, model, method, *args, **kwargs):
        if model == "hr.employee" and method == "search_read":
            return list(self._employees)
        raise AssertionError(f"unexpected execute {model}.{method}")

    async def close(self):
        self.closed = True


@pytest.fixture
def captured(monkeypatch):
    """Patch build_user_client + execute_* and capture calls."""
    calls = {}

    fake_client = FakeUserClient()

    def fake_build(config, login, api_key):
        calls["build"] = {"login": login, "api_key": api_key}
        return fake_client

    async def fake_crud(name, arguments, client):
        calls["crud"] = {"name": name, "arguments": arguments, "client": client}
        return [TextContent(type="text", text="crud-ok")]

    async def fake_employee(name, arguments, client, employee_id):
        calls["employee"] = {"name": name, "employee_id": employee_id, "client": client}
        return [TextContent(type="text", text="emp-ok")]

    async def fake_sign(name, arguments, client, employee_id):
        calls["sign"] = {"name": name, "employee_id": employee_id}
        return [TextContent(type="text", text="sign-ok")]

    monkeypatch.setattr(tools_adapter, "build_user_client", fake_build)
    monkeypatch.setattr(tools_adapter, "execute_tool", fake_crud)
    monkeypatch.setattr(tools_adapter, "execute_employee_tool", fake_employee)
    monkeypatch.setattr(tools_adapter, "execute_sign_tool", fake_sign)
    calls["_client"] = fake_client
    return calls


@pytest.mark.asyncio
async def test_dispatch_crud_uses_per_user_client(captured):
    out = await tools_adapter.dispatch(object(), "search_records", {"model": "res.partner"}, "a@x.com", "KEY")
    assert out[0].text == "crud-ok"
    # per-user client built from the caller's email + minted key
    assert captured["build"] == {"login": "a@x.com", "api_key": "KEY"}
    assert "employee" not in captured and "sign" not in captured
    assert captured["_client"].closed is True  # client cleaned up


@pytest.mark.asyncio
async def test_dispatch_employee_resolves_employee_id(captured):
    out = await tools_adapter.dispatch(object(), "get_my_profile", {}, "a@x.com", "KEY")
    assert out[0].text == "emp-ok"
    assert captured["employee"]["name"] == "get_my_profile"
    assert captured["employee"]["employee_id"] == 4265  # resolved from hr.employee


@pytest.mark.asyncio
async def test_dispatch_sign_tool(captured):
    out = await tools_adapter.dispatch(object(), "list_sign_templates", {}, "a@x.com", "KEY")
    assert out[0].text == "sign-ok"
    assert captured["sign"]["employee_id"] == 4265


@pytest.mark.asyncio
async def test_dispatch_employee_no_record(monkeypatch):
    monkeypatch.setattr(tools_adapter, "build_user_client", lambda config, login, api_key: FakeUserClient(employees=[]))
    out = await tools_adapter.dispatch(object(), "get_my_profile", {}, "a@x.com", "KEY")
    assert "no odoo employee" in out[0].text.lower()
