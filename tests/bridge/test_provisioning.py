"""Unit tests for email->uid resolution and per-user key minting (mocked Odoo)."""

import pytest

from odoo_mcp_bridge.provisioning import (
    ProvisioningError,
    is_addon_installed,
    mint_user_key,
    resolve_odoo_uid,
)

pytestmark = [pytest.mark.unit]


class FakeAdminClient:
    """Minimal async stand-in for OdooClient.execute."""

    def __init__(self, *, users=None, mint_result="minted-key", mint_error=None, addon_installed=True):
        self._users = users or []
        self._mint_result = mint_result
        self._mint_error = mint_error
        self._addon_installed = addon_installed
        self.calls = []

    async def execute(self, model, method, *args, **kwargs):
        self.calls.append((model, method, args, kwargs))
        if model == "ir.module.module" and method == "search_read":
            return [{"id": 1}] if self._addon_installed else []
        if model == "res.users" and method == "search_read":
            return list(self._users)
        if model == "res.users" and method == "mcp_mint_apikey":
            if self._mint_error:
                raise self._mint_error
            return self._mint_result
        raise AssertionError(f"unexpected call {model}.{method}")


@pytest.mark.asyncio
async def test_resolve_uid_found():
    client = FakeAdminClient(users=[{"id": 42, "login": "a@x.com"}])
    assert await resolve_odoo_uid(client, "A@X.com") == 42


@pytest.mark.asyncio
async def test_resolve_uid_not_found():
    client = FakeAdminClient(users=[])
    assert await resolve_odoo_uid(client, "ghost@x.com") is None


@pytest.mark.asyncio
async def test_mint_success_passes_uid_name_ttl():
    client = FakeAdminClient(users=[{"id": 7, "login": "a@x.com"}], mint_result="KEY-7")
    key = await mint_user_key(client, "a@x.com", ttl_days=14)
    assert key == "KEY-7"
    mint_call = [c for c in client.calls if c[1] == "mcp_mint_apikey"][0]
    assert mint_call[2] == (7, "mcp:a@x.com", 14)


@pytest.mark.asyncio
async def test_mint_no_user_raises():
    client = FakeAdminClient(users=[])
    with pytest.raises(ProvisioningError):
        await mint_user_key(client, "ghost@x.com", ttl_days=30)


@pytest.mark.asyncio
async def test_mint_missing_addon_raises_clear_error():
    err = Exception("The method 'res.users.mcp_mint_apikey' does not exist")
    client = FakeAdminClient(users=[{"id": 7, "login": "a@x.com"}], mint_error=err)
    with pytest.raises(ProvisioningError) as ei:
        await mint_user_key(client, "a@x.com", ttl_days=30)
    assert "addon" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_is_addon_installed_true_and_false():
    assert await is_addon_installed(FakeAdminClient(addon_installed=True)) is True
    assert await is_addon_installed(FakeAdminClient(addon_installed=False)) is False
