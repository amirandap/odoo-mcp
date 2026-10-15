"""
Unit tests for the shared fields_get cache used by search-domain validation.

Vault-linked users get a brand-new OdooClient on every request, so a cache
living on the client instance is thrown away each time and every filtered
search pays an extra fields_get round trip. The cache must outlive clients
(keyed by Odoo url/db/login/model) without leaking field visibility across
different Odoo users.

Run with: pytest tests/unit/test_fields_cache.py -v -m unit
"""

from unittest.mock import AsyncMock

import pytest

pytestmark = [pytest.mark.unit]


def _client(login="a@x.com", url="https://odoo.test", db="d"):
    client = AsyncMock()
    client.url = url
    client.db = db
    client.username = login
    client.fields_get.return_value = {"id": {}, "name": {}}
    return client


@pytest.fixture(autouse=True)
def _clear_cache():
    from odoo_mcp_server.tools.records import clear_fields_cache

    clear_fields_cache()
    yield
    clear_fields_cache()


@pytest.mark.asyncio
async def test_second_call_same_client_hits_cache():
    from odoo_mcp_server.tools.records import cached_model_fields

    client = _client()
    await cached_model_fields(client, "res.partner")
    await cached_model_fields(client, "res.partner")

    assert client.fields_get.await_count == 1


@pytest.mark.asyncio
async def test_new_client_instance_same_user_reuses_cache():
    """The per-request personal_client case."""
    from odoo_mcp_server.tools.records import cached_model_fields

    first, second = _client(), _client()
    await cached_model_fields(first, "res.partner")
    await cached_model_fields(second, "res.partner")

    assert first.fields_get.await_count == 1
    second.fields_get.assert_not_called()


@pytest.mark.asyncio
async def test_different_odoo_user_does_not_share_cache():
    """Field visibility can depend on the user's groups."""
    from odoo_mcp_server.tools.records import cached_model_fields

    admin, limited = _client("admin@x.com"), _client("limited@x.com")
    await cached_model_fields(admin, "res.partner")
    await cached_model_fields(limited, "res.partner")

    limited.fields_get.assert_awaited_once()


@pytest.mark.asyncio
async def test_different_model_is_a_separate_entry():
    from odoo_mcp_server.tools.records import cached_model_fields

    client = _client()
    await cached_model_fields(client, "res.partner")
    await cached_model_fields(client, "sale.order")

    assert client.fields_get.await_count == 2
