"""Regression: tools/list must show generic CRUD tools to a user who has a
personal Odoo credential on file, even though they don't hold the
odoo.read/odoo.write scopes that gate everyone else.

Bug found in production: handle_tools_call already let a vault-linked user
CALL search_records/create_record/etc. (see test_personal_odoo_credentials.py),
but handle_tools_list still filtered those tools OUT of the list using only
the scope check - so a linked user saw only the employee self-service tools
and never discovered the CRUD tools existed at all.
"""
import importlib

import pytest

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
    return module


@pytest.fixture
def vault(tmp_path, http_server_module):
    from cryptography.fernet import Fernet

    from odoo_mcp_server.odoo.user_vault import UserCredentialVault

    return UserCredentialVault(str(tmp_path / "keys.json"), Fernet.generate_key().decode())


class TestToolsListWithoutVault:
    async def test_user_without_odoo_scopes_does_not_see_crud_tools(self, http_server_module):
        user = {"email": "someone@example.com", "scopes": ["odoo.hr.profile"]}

        result = await http_server_module.handle_tools_list(user)

        names = {t["name"] for t in result["tools"]}
        assert "search_records" not in names
        assert "get_my_profile" in names

    async def test_admin_allowlist_user_sees_crud_tools(self, http_server_module):
        user = {"email": "admin@acme.dev", "scopes": ["odoo.read", "odoo.write"]}

        result = await http_server_module.handle_tools_list(user)

        names = {t["name"] for t in result["tools"]}
        assert "search_records" in names
        assert "create_record" in names


class TestToolsListWithPersonalCredential:
    async def test_vault_linked_user_sees_crud_tools_without_odoo_scopes(
        self, http_server_module, monkeypatch, vault
    ):
        monkeypatch.setattr(http_server_module, "user_vault", vault)
        vault.put_credentials("alice@example.com", "alice@acme.odoo", "her-own-key")

        # No odoo.read/odoo.write - only the automatic employee defaults.
        user = {"email": "alice@example.com", "scopes": ["odoo.hr.profile"]}

        result = await http_server_module.handle_tools_list(user)

        names = {t["name"] for t in result["tools"]}
        assert "search_records" in names
        assert "create_record" in names
        assert "get_my_profile" in names

    async def test_user_without_a_vault_entry_is_unaffected_by_vault_being_enabled(
        self, http_server_module, monkeypatch, vault
    ):
        monkeypatch.setattr(http_server_module, "user_vault", vault)
        # Vault configured, but no entry for THIS user.
        user = {"email": "nobody-linked@example.com", "scopes": ["odoo.hr.profile"]}

        result = await http_server_module.handle_tools_list(user)

        names = {t["name"] for t in result["tools"]}
        assert "search_records" not in names
