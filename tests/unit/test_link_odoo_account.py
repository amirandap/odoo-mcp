"""Tests for the self-service "link my own Odoo account" flow.

A user shouldn't need an admin to run scripts/set_user_odoo_key.py for them.
GET /link-odoo-account starts a dedicated (separate from the MCP-client proxy
flow) OAuth round-trip against the SAME configured IdP purely to confirm the
caller's verified email; /callback recognizes that purpose and, instead of
forwarding the code to an MCP client, exchanges it itself, resolves the
email via the existing resource_server.validate_token_async() (same path
used for real MCP requests - no new crypto), and renders a small form asking
for the user's own Odoo login + API key. POST /link-odoo-account/submit test-
authenticates those credentials against the configured Odoo instance before
storing them, so a typo doesn't silently break the person's access.
"""
import importlib

import pytest

pytestmark = [pytest.mark.unit]


@pytest.fixture
def vault_client(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ODOO_URL", "https://odoo.example.test")
    monkeypatch.setenv("ODOO_DB", "test_db")
    monkeypatch.setenv("ODOO_API_KEY", "shared-service-key")
    monkeypatch.setenv("OAUTH_DEV_MODE", "true")
    monkeypatch.setenv("OAUTH_PROVIDER", "custom")
    monkeypatch.setenv("OAUTH_AUTHORIZATION_ENDPOINT", "https://auth.example.com/authorize")
    monkeypatch.setenv("OAUTH_TOKEN_ENDPOINT", "https://auth.example.com/oauth/token")
    monkeypatch.setenv("OAUTH_JWKS_URI", "https://auth.example.com/jwks")
    monkeypatch.setenv("OAUTH_ISSUER", "https://auth.example.com")
    monkeypatch.setenv("OAUTH_REDIRECT_URI", "https://mcp-server.example.com/callback")
    monkeypatch.setenv("OAUTH_RESOURCE_IDENTIFIER", "https://mcp-server.example.com")
    monkeypatch.setenv("OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("OAUTH_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("USER_KEY_VAULT_PATH", str(tmp_path / "keys.json"))
    monkeypatch.setenv("USER_KEY_VAULT_ENCRYPTION_KEY", Fernet.generate_key().decode())

    import odoo_mcp_server.http_server as module

    importlib.reload(module)
    return TestClient(module.app), module


@pytest.fixture
def no_vault_client(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ODOO_URL", "https://odoo.example.test")
    monkeypatch.setenv("ODOO_DB", "test_db")
    monkeypatch.setenv("ODOO_API_KEY", "shared-service-key")
    monkeypatch.setenv("OAUTH_DEV_MODE", "true")
    monkeypatch.delenv("USER_KEY_VAULT_PATH", raising=False)
    monkeypatch.delenv("USER_KEY_VAULT_ENCRYPTION_KEY", raising=False)

    import odoo_mcp_server.http_server as module

    importlib.reload(module)
    return TestClient(module.app), module


class TestLinkOdooAccountDisabled:
    def test_returns_404_when_vault_not_configured(self, no_vault_client):
        client, _module = no_vault_client
        response = client.get("/link-odoo-account", follow_redirects=False)
        assert response.status_code == 404


class TestLinkOdooAccountStart:
    def test_redirects_to_authorization_endpoint(self, vault_client):
        client, module = vault_client
        response = client.get("/link-odoo-account", follow_redirects=False)

        assert response.status_code == 302
        location = response.headers["location"]
        assert location.startswith("https://auth.example.com/authorize")
        assert "scope=openid+email+profile" in location
        assert "redirect_uri=https%3A%2F%2Fmcp-server.example.com%2Fcallback" in location

    def test_stores_session_with_link_purpose(self, vault_client):
        client, module = vault_client
        module._pending_auth_sessions.clear()

        client.get("/link-odoo-account", follow_redirects=False)

        assert len(module._pending_auth_sessions) == 1
        session = next(iter(module._pending_auth_sessions.values()))
        assert session["purpose"] == "link_odoo_account"


class TestCallbackLinkPurpose:
    def _mock_token_exchange_and_identity(self, monkeypatch, module, email="alice@example.com"):
        import httpx

        async def fake_post(self, url, data=None, **kwargs):
            assert url == "https://auth.example.com/oauth/token"
            return httpx.Response(200, json={"access_token": "upstream-access-token", "token_type": "Bearer"})

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

        async def fake_validate(token):
            assert token == "upstream-access-token"
            return {"email": email, "email_verified": True}

        monkeypatch.setattr(module._get_resource_server(), "validate_token_async", fake_validate)

    def test_renders_form_with_link_token(self, vault_client, monkeypatch):
        client, module = vault_client
        module._store_auth_session(state="link-state-1", client_redirect_uri="", purpose="link_odoo_account")
        self._mock_token_exchange_and_identity(monkeypatch, module)

        response = client.get("/callback", params={"code": "the-code", "state": "link-state-1"})

        assert response.status_code == 200
        assert "odoo_login" in response.text
        assert "odoo_api_key" in response.text
        assert "link_token" in response.text
        # The raw email must never be handed back to the browser as a
        # client-controlled field - it's tied server-side to the link_token.
        assert "alice@example.com" not in response.text

    def test_creates_a_pending_link_session_keyed_by_email(self, vault_client, monkeypatch):
        client, module = vault_client
        module._store_auth_session(state="link-state-2", client_redirect_uri="", purpose="link_odoo_account")
        self._mock_token_exchange_and_identity(monkeypatch, module, email="bob@example.com")

        client.get("/callback", params={"code": "the-code", "state": "link-state-2"})

        assert len(module._pending_link_sessions) == 1
        session = next(iter(module._pending_link_sessions.values()))
        assert session["email"] == "bob@example.com"

    def test_unverified_email_is_rejected(self, vault_client, monkeypatch):
        client, module = vault_client
        module._store_auth_session(state="link-state-3", client_redirect_uri="", purpose="link_odoo_account")

        import httpx

        async def fake_post(self, url, data=None, **kwargs):
            return httpx.Response(200, json={"access_token": "tok"})

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

        async def fake_validate(token):
            return {"email": "carol@example.com", "email_verified": False}

        monkeypatch.setattr(module._get_resource_server(), "validate_token_async", fake_validate)

        response = client.get("/callback", params={"code": "the-code", "state": "link-state-3"})

        assert response.status_code == 400
        assert len(module._pending_link_sessions) == 0

    def test_token_exchange_failure_shows_error(self, vault_client, monkeypatch):
        client, module = vault_client
        module._store_auth_session(state="link-state-4", client_redirect_uri="", purpose="link_odoo_account")

        import httpx

        async def fake_post(self, url, data=None, **kwargs):
            return httpx.Response(400, json={"error": "invalid_grant"})

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

        response = client.get("/callback", params={"code": "the-code", "state": "link-state-4"})

        assert response.status_code == 400

    def test_normal_mcp_client_callback_is_unaffected(self, vault_client):
        """Regression: a session with no 'purpose' (the existing MCP-client
        proxy flow) must still take the old forwarding path unchanged."""
        client, module = vault_client
        module._store_auth_session(state="normal-state", client_redirect_uri="http://127.0.0.1:9999/callback")

        response = client.get(
            "/callback", params={"code": "some-code", "state": "normal-state"}, follow_redirects=False
        )

        assert response.status_code == 302
        assert response.headers["location"].startswith("http://127.0.0.1:9999/callback")


class TestLinkOdooAccountSubmit:
    def _prime_link_session(self, module, email="alice@example.com"):
        link_token = "test-link-token"
        module._pending_link_sessions[link_token] = {"email": email, "created_at": module.time.time()}
        return link_token

    def test_valid_credentials_are_stored_and_show_success(self, vault_client, monkeypatch):
        client, module = vault_client
        link_token = self._prime_link_session(module)

        async def fake_authenticate(self):
            return 42

        monkeypatch.setattr(module.OdooClient, "authenticate", fake_authenticate)

        response = client.post(
            "/link-odoo-account/submit",
            data={"link_token": link_token, "odoo_login": "alice@vivaldi.odoo", "odoo_api_key": "her-real-key"},
        )

        assert response.status_code == 200
        assert "success" in response.text.lower() or "listo" in response.text.lower()
        assert module.user_vault.get_credentials("alice@example.com") == ("alice@vivaldi.odoo", "her-real-key")
        # Single-use: the link session is gone after success.
        assert link_token not in module._pending_link_sessions

    def test_invalid_odoo_credentials_are_rejected_and_not_stored(self, vault_client, monkeypatch):
        client, module = vault_client
        link_token = self._prime_link_session(module)

        from odoo_mcp_server.odoo.exceptions import OdooAuthenticationError

        async def fake_authenticate(self):
            raise OdooAuthenticationError("bad credentials")

        monkeypatch.setattr(module.OdooClient, "authenticate", fake_authenticate)

        response = client.post(
            "/link-odoo-account/submit",
            data={"link_token": link_token, "odoo_login": "alice@vivaldi.odoo", "odoo_api_key": "typo-key"},
        )

        assert response.status_code == 400
        assert module.user_vault.get_credentials("alice@example.com") is None
        # Retryable: the same link_token must still work for a second attempt.
        assert link_token in module._pending_link_sessions

    def test_missing_link_token_is_rejected(self, vault_client, monkeypatch):
        client, module = vault_client

        response = client.post(
            "/link-odoo-account/submit",
            data={"link_token": "does-not-exist", "odoo_login": "alice@vivaldi.odoo", "odoo_api_key": "k"},
        )

        assert response.status_code == 400

    def test_expired_link_token_is_rejected(self, vault_client, monkeypatch):
        client, module = vault_client
        link_token = "expired-token"
        module._pending_link_sessions[link_token] = {"email": "alice@example.com", "created_at": 0}

        response = client.post(
            "/link-odoo-account/submit",
            data={"link_token": link_token, "odoo_login": "alice@vivaldi.odoo", "odoo_api_key": "k"},
        )

        assert response.status_code == 400

    def test_disabled_when_vault_not_configured(self, no_vault_client):
        client, _module = no_vault_client

        response = client.post(
            "/link-odoo-account/submit",
            data={"link_token": "x", "odoo_login": "a", "odoo_api_key": "b"},
        )

        assert response.status_code == 404
