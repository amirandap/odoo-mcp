"""Tests for OAuth-proxy client_storage backend selection."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from odoo_mcp_bridge.oauth_storage import build_client_storage

pytestmark = [pytest.mark.unit]


def _cfg(**over):
    base = dict(
        oauth_client_storage="",
        oauth_client_storage_collection="oauth-proxy-state",
        oauth_client_storage_redis_url=None,
        gcp_project=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.mark.parametrize("backend", ["", "disk", "default", "none"])
def test_default_disk_returns_none(backend):
    # None => FastMCP's built-in disk store (caller logs the ephemeral-on-Cloud-Run warning).
    assert build_client_storage(_cfg(oauth_client_storage=backend)) is None


def test_memory_backend():
    # The py-key-value driver ships with the bridge extras; skip if a bare [dev] env
    # (e.g. the upstream unit-tests job) doesn't have it. The routing/fail-fast tests above
    # don't need it.
    pytest.importorskip("key_value.aio.stores.memory")
    from key_value.aio.stores.memory import MemoryStore

    store = build_client_storage(_cfg(oauth_client_storage="memory"))
    assert isinstance(store, MemoryStore)


def test_firestore_requires_project():
    with pytest.raises(RuntimeError, match="GCP_PROJECT"):
        build_client_storage(_cfg(oauth_client_storage="firestore", gcp_project=None))


def test_redis_requires_url():
    with pytest.raises(RuntimeError, match="OAUTH_CLIENT_STORAGE_REDIS_URL"):
        build_client_storage(_cfg(oauth_client_storage="redis", oauth_client_storage_redis_url=None))


def test_unknown_backend_raises():
    with pytest.raises(RuntimeError, match="Unknown OAUTH_CLIENT_STORAGE"):
        build_client_storage(_cfg(oauth_client_storage="banana"))
