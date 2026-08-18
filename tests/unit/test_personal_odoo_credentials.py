"""Tests for the personal-Odoo-credential override in handle_tools_call.

A user whose email is registered in the user credential vault (see
odoo/user_vault.py) gets generic CRUD tools that run AS THEMSELVES against
Odoo - bypassing the CRUD_ADMIN_EMAILS scope gate entirely, because Odoo's
own permissions on their account are the real gate. Users without a vault
entry (the common case - e.g. Adela/Eduardo staying on the shared master
token) must see byte-for-byte the same behavior as before this feature
existed. Employee/sign self-service tools are untouched either way - they
keep using the shared service account.
"""
import importlib

import pytest
from fastapi import HTTPException

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
def http_server_module(monkeypatch):
    monkeypatch.setenv("ODOO_URL", "https://odoo.example.test")
    monkeypatch.setenv("ODOO_DB", "test_db")
    monkeypatch.setenv("ODOO_API_KEY", "shared-service-key")
    monkeypatch.setenv("ODOO_USERNAME", "svc@example.test")
    monkeypatch.setenv("OAUTH_DEV_MODE", "true")
    monkeypatch.delenv("USER_KEY_VAULT_PATH", raising=False)
    monkeypatch.delenv("USER_KEY_VAULT_ENCRYPTION_KEY", raising=False)

    import odoo_mcp_server.http_server as module

    importlib.reload(module)
    # Normally set by the FastAPI lifespan on startup; set it directly here
    # since these tests call handle_tools_call() without booting the app.
    module.odoo_client = module.OdooClient(
        url="https://odoo.example.test", db="test_db", api_key="shared-service-key", username="svc@example.test"
    )
    return module


@pytest.fixture
def vault(tmp_path, http_server_module):
    from cryptography.fernet import Fernet

    from odoo_mcp_server.odoo.user_vault import UserCredentialVault

    return UserCredentialVault(str(tmp_path / "keys.json"), Fernet.generate_key().decode())


def _capture_execute_tool(monkeypatch, module):
    captured = {}

    async def fake_execute_tool(name, arguments, client):
        captured["name"] = name
        captured["client"] = client
        return []

    monkeypatch.setattr(module, "execute_tool", fake_execute_tool)
    return captured


class TestNoVaultEntryUnchangedBehavior:
    """Without a personal-key vault configured at all (default deployment)."""

    async def test_crud_tool_without_odoo_read_scope_is_rejected(self, http_server_module):
        user = {"email": "adela@example.com", "scopes": []}
        params = {"name": "search_records", "arguments": {"model": "res.partner"}}

        with pytest.raises(HTTPException) as exc_info:
            await http_server_module.handle_tools_call(params, user)
        assert exc_info.value.status_code == 403

    async def test_crud_tool_with_odoo_read_scope_uses_shared_client(self, http_server_module, monkeypatch):
        captured = _capture_execute_tool(monkeypatch, http_server_module)
        user = {"email": "adela@example.com", "scopes": ["odoo.read"]}
        params = {"name": "search_records", "arguments": {"model": "res.partner"}}

        await http_server_module.handle_tools_call(params, user)

        assert captured["client"] is http_server_module.odoo_client


class TestPersonalCredentialOverride:
    async def test_crud_tool_uses_personal_client_when_vault_has_entry(
        self, http_server_module, monkeypatch, vault
    ):
        monkeypatch.setattr(http_server_module, "user_vault", vault)
        vault.put_credentials("yosimar@example.com", "yosimar@vivaldi.odoo", "her-own-key")
        captured = _capture_execute_tool(monkeypatch, http_server_module)

        # Deliberately NO odoo.read/odoo.write scope - the vault entry alone must be enough.
        user = {"email": "yosimar@example.com", "scopes": []}
        params = {"name": "search_records", "arguments": {"model": "res.partner"}}

        await http_server_module.handle_tools_call(params, user)

        client = captured["client"]
        assert client is not http_server_module.odoo_client
        assert client.username == "yosimar@vivaldi.odoo"
        assert client.api_key == "her-own-key"

    async def test_crud_tool_without_vault_entry_still_gated_by_scope(
        self, http_server_module, monkeypatch, vault
    ):
        monkeypatch.setattr(http_server_module, "user_vault", vault)
        # Vault is configured but has no entry for this user.
        user = {"email": "someone-else@example.com", "scopes": []}
        params = {"name": "search_records", "arguments": {"model": "res.partner"}}

        with pytest.raises(HTTPException) as exc_info:
            await http_server_module.handle_tools_call(params, user)
        assert exc_info.value.status_code == 403

    async def test_adela_and_eduardo_style_admin_allowlist_still_works_alongside_vault(
        self, http_server_module, monkeypatch, vault
    ):
        """Regression: turning the vault feature on must not affect users who
        keep using the shared master token (CRUD_ADMIN_EMAILS)."""
        monkeypatch.setattr(http_server_module, "user_vault", vault)
        captured = _capture_execute_tool(monkeypatch, http_server_module)

        user = {"email": "admin@vivaldi.do", "scopes": ["odoo.read", "odoo.write"]}
        params = {"name": "search_records", "arguments": {"model": "res.partner"}}

        await http_server_module.handle_tools_call(params, user)

        assert captured["client"] is http_server_module.odoo_client

    async def test_employee_tool_ignores_personal_vault_entry(self, http_server_module, monkeypatch, vault):
        """Self-service tools keep using the shared service account even when
        the caller also has a personal Odoo credential on file."""
        monkeypatch.setattr(http_server_module, "user_vault", vault)
        vault.put_credentials("yosimar@example.com", "yosimar@vivaldi.odoo", "her-own-key")

        captured = {}

        async def fake_execute_employee_tool(tool_name, arguments, client, employee_id):
            captured["client"] = client
            return []

        monkeypatch.setattr(http_server_module, "execute_employee_tool", fake_execute_employee_tool)

        async def fake_get_employee_for_user(claims, client):
            return {"id": 42}

        monkeypatch.setattr(http_server_module, "get_employee_for_user", fake_get_employee_for_user)

        user = {
            "email": "yosimar@example.com",
            "scopes": ["odoo.hr.profile"],
            "claims": {"email": "yosimar@example.com"},
        }
        params = {"name": "get_my_profile", "arguments": {}}

        await http_server_module.handle_tools_call(params, user)

        assert captured["client"] is http_server_module.odoo_client
