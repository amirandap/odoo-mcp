"""Unit tests for bridge config — email-alias resolution."""

import pytest

pytestmark = [pytest.mark.unit]


@pytest.fixture
def base_env(monkeypatch):
    for k, v in {
        "ODOO_URL": "https://o.example.com",
        "ODOO_DB": "db",
        "ODOO_USERNAME": "svc@example.com",
        "ODOO_API_KEY": "k",
        "OAUTH_CLIENT_ID": "cid",
        "OAUTH_CLIENT_SECRET": "sec",
        "OAUTH_RESOURCE_IDENTIFIER": "https://b.example.com",
        "SESSION_SECRET": "s",
        "BRIDGE_ALLOWED_DOMAINS": "*",
    }.items():
        monkeypatch.setenv(k, v)
    for k in ("BRIDGE_EMAIL_ALIASES", "BRIDGE_ALLOWED_EMAILS", "TOKEN_STORAGE_BACKEND", "TOKEN_ENCRYPTION_KEY"):
        monkeypatch.delenv(k, raising=False)


def test_no_aliases_is_noop_but_lowercases(base_env):
    from odoo_mcp_bridge.config import load_config

    c = load_config()
    assert c.email_aliases == {}
    assert c.canonical_email("Alice@Example.com") == "alice@example.com"


def test_alias_map_json(base_env, monkeypatch):
    monkeypatch.setenv("BRIDGE_EMAIL_ALIASES", '{"alice.personal@gmail.com": "alice@example.com"}')
    from odoo_mcp_bridge.config import load_config

    c = load_config()
    assert c.canonical_email("alice.personal@gmail.com") == "alice@example.com"
    assert c.canonical_email("Alice.Personal@Gmail.com") == "alice@example.com"  # case-insensitive
    assert c.canonical_email("someone@else.com") == "someone@else.com"  # unmapped -> itself


def test_alias_map_csv(base_env, monkeypatch):
    monkeypatch.setenv("BRIDGE_EMAIL_ALIASES", "a@x.com=canon@x.com, b@y.com=canon@x.com")
    from odoo_mcp_bridge.config import load_config

    c = load_config()
    assert c.canonical_email("a@x.com") == "canon@x.com"
    assert c.canonical_email("b@y.com") == "canon@x.com"
