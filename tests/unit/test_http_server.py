"""
TDD Tests for HTTP MCP Server

These tests define the expected behavior of the HTTP MCP server.
They WILL FAIL until the implementation is complete.

Run with: pytest tests/unit/test_http_server.py -v
"""

import pytest

pytestmark = [pytest.mark.unit]


class TestHTTPServerExists:
    """Tests that verify the HTTP server module exists and can be imported."""

    def test_http_server_module_exists(self):
        """
        EXPECTED: http_server.py module should exist and be importable.
        FAILS UNTIL: src/odoo_mcp_server/http_server.py is created.
        """
        from odoo_mcp_server import http_server

        assert http_server is not None

    def test_http_server_has_app(self):
        """
        EXPECTED: HTTP server should expose a FastAPI app instance.
        FAILS UNTIL: FastAPI app is created in http_server.py.
        """
        from odoo_mcp_server.http_server import app

        assert app is not None
        assert hasattr(app, "routes")

    def test_http_server_has_main_function(self):
        """
        EXPECTED: HTTP server should have a main() entry point.
        FAILS UNTIL: main() function is implemented.
        """
        from odoo_mcp_server.http_server import main

        assert callable(main)


class TestHTTPServerEndpoints:
    """Tests for required HTTP endpoints."""

    @pytest.fixture
    def client(self):
        """Create test client for HTTP server."""
        from fastapi.testclient import TestClient

        from odoo_mcp_server.http_server import app

        return TestClient(app)

    def test_health_endpoint(self, client):
        """
        EXPECTED: GET /health returns 200 with status.
        FAILS UNTIL: Health endpoint is implemented.
        """
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    def test_oauth_protected_resource_metadata(self, client):
        """
        EXPECTED: GET /.well-known/oauth-protected-resource returns RFC 9728 metadata.
        FAILS UNTIL: OAuth metadata endpoint is implemented.
        """
        response = client.get("/.well-known/oauth-protected-resource")

        assert response.status_code == 200
        metadata = response.json()

        # RFC 9728 required fields
        assert "resource" in metadata
        assert "authorization_servers" in metadata
        assert isinstance(metadata["authorization_servers"], list)
        # Server acts as its own authorization server (proxies to Google)
        assert len(metadata["authorization_servers"]) >= 1

    def test_mcp_endpoint_exists(self, client):
        """
        EXPECTED: POST /mcp endpoint exists for MCP JSON-RPC.
        FAILS UNTIL: MCP endpoint is implemented.
        """
        # Without auth, should get 401
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "tools/list", "id": 1}
        )

        # 401 means endpoint exists but requires auth
        assert response.status_code == 401

    def test_mcp_endpoint_requires_bearer_token(self, client):
        """
        EXPECTED: /mcp endpoint requires Bearer token in Authorization header.
        FAILS UNTIL: OAuth middleware is implemented.
        """
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "tools/list", "id": 1}
        )

        assert response.status_code == 401
        assert "WWW-Authenticate" in response.headers
        assert "Bearer" in response.headers["WWW-Authenticate"]

    def test_callback_endpoint_for_oauth(self, client):
        """
        EXPECTED: GET /callback handles OAuth authorization code callback.
        FAILS UNTIL: OAuth callback is implemented.
        """
        response = client.get("/callback?code=test&state=test")

        # Should handle the callback (might redirect or return HTML)
        assert response.status_code in [200, 302, 303]


class TestDynamicClientRegistration:
    """Tests for RFC 7591 Dynamic Client Registration endpoint."""

    @pytest.fixture
    def client(self, monkeypatch):
        """Create test client for HTTP server with required env vars."""
        import importlib

        from fastapi.testclient import TestClient

        monkeypatch.setenv("ODOO_URL", "http://localhost:8069")
        monkeypatch.setenv("ODOO_DB", "test_db")
        monkeypatch.setenv("OAUTH_DEV_MODE", "true")

        import odoo_mcp_server.http_server as http_server_module

        importlib.reload(http_server_module)
        return TestClient(http_server_module.app)

    def test_register_returns_201_with_client_id(self, client):
        """POST /register returns 201 with a valid client_id."""
        response = client.post(
            "/register",
            json={
                "client_name": "mcp-remote",
                "redirect_uris": ["http://127.0.0.1:3000/oauth/callback"],
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert "client_id" in data
        assert len(data["client_id"]) == 36  # UUID format
        assert data["client_name"] == "mcp-remote"
        assert data["redirect_uris"] == ["http://127.0.0.1:3000/oauth/callback"]
        assert data["token_endpoint_auth_method"] == "client_secret_post"
        assert "client_secret" in data
        assert len(data["client_secret"]) == 36  # UUID format

    def test_register_with_empty_body(self, client):
        """POST /register works even with empty/missing body."""
        response = client.post("/register", content=b"")

        assert response.status_code == 201
        data = response.json()
        assert "client_id" in data
        assert data["client_name"] == "mcp-client"

    def test_register_generates_unique_client_ids(self, client):
        """Each registration returns a unique client_id."""
        body = {"client_name": "test"}
        id1 = client.post("/register", json=body).json()["client_id"]
        id2 = client.post("/register", json=body).json()["client_id"]

        assert id1 != id2

    def test_register_does_not_require_auth(self, client):
        """POST /register does not require a Bearer token."""
        response = client.post(
            "/register",
            json={"client_name": "test"},
        )

        # Should NOT be 401
        assert response.status_code == 201

    def test_authorization_server_metadata_includes_registration_endpoint(self, client):
        """
        GET /.well-known/oauth-authorization-server includes registration_endpoint.
        Required by MCP Authorization spec for mcp-remote compatibility.
        """
        response = client.get("/.well-known/oauth-authorization-server")

        assert response.status_code == 200
        metadata = response.json()
        assert "registration_endpoint" in metadata
        assert metadata["registration_endpoint"].endswith("/register")


class TestAPIKeyAuthentication:
    """Tests for API key authentication (CLI clients like Claude Code)."""

    @pytest.fixture
    def api_key_client(self, monkeypatch):
        """Create test client with API key auth configured."""
        import importlib

        from fastapi.testclient import TestClient

        monkeypatch.setenv("ODOO_URL", "http://localhost:8069")
        monkeypatch.setenv("ODOO_DB", "test_db")
        monkeypatch.setenv("MCP_API_KEY", "test-api-key-secret")
        monkeypatch.setenv("MCP_API_KEY_EMAIL", "svc-mcp@example.com")
        # Ensure dev mode is off so we test real auth paths
        monkeypatch.delenv("OAUTH_DEV_MODE", raising=False)

        import odoo_mcp_server.http_server as http_server_module

        importlib.reload(http_server_module)
        return TestClient(http_server_module.app)

    def test_api_key_grants_access(self, api_key_client):
        """Valid API key should authenticate and grant access to /mcp."""
        response = api_key_client.post(
            "/mcp",
            headers={"Authorization": "Bearer test-api-key-secret"},
            json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
        )

        assert response.status_code == 200
        result = response.json()
        assert "result" in result
        assert "tools" in result["result"]

    def test_api_key_sets_correct_email(self, api_key_client):
        """API key auth should use the configured email identity."""
        response = api_key_client.post(
            "/mcp",
            headers={"Authorization": "Bearer test-api-key-secret"},
            json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
        )

        assert response.status_code == 200

    def test_wrong_api_key_is_rejected(self, api_key_client):
        """Invalid API key should not grant access."""
        response = api_key_client.post(
            "/mcp",
            headers={"Authorization": "Bearer wrong-key"},
            json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
        )

        # Falls through to Google token validation which will also fail
        assert response.status_code == 401

    def test_no_api_key_configured_skips_check(self, monkeypatch):
        """When MCP_API_KEY is not set, API key auth is skipped."""
        import importlib

        from fastapi.testclient import TestClient

        monkeypatch.setenv("ODOO_URL", "http://localhost:8069")
        monkeypatch.setenv("ODOO_DB", "test_db")
        monkeypatch.delenv("MCP_API_KEY", raising=False)
        monkeypatch.delenv("OAUTH_DEV_MODE", raising=False)

        import odoo_mcp_server.http_server as http_server_module

        importlib.reload(http_server_module)
        client = TestClient(http_server_module.app)

        response = client.post(
            "/mcp",
            headers={"Authorization": "Bearer some-random-token"},
            json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
        )

        # Should fall through to Google token validation (and fail)
        assert response.status_code == 401


class TestHTTPServerMCPProtocol:
    """Tests for MCP protocol over HTTP."""

    @pytest.fixture
    def authenticated_client(self, monkeypatch):
        """Create test client with dev mode authentication enabled."""
        import importlib

        from fastapi.testclient import TestClient

        import odoo_mcp_server.http_server as http_server_module

        # Enable OAuth dev mode to bypass token validation
        monkeypatch.setenv("OAUTH_DEV_MODE", "true")

        # Reload to pick up env var change
        importlib.reload(http_server_module)

        client = TestClient(http_server_module.app)
        return client

    def test_mcp_tools_list(self, authenticated_client):
        """
        EXPECTED: tools/list returns available MCP tools.
        FAILS UNTIL: MCP protocol handler is implemented.
        """
        response = authenticated_client.post(
            "/mcp",
            headers={"Authorization": "Bearer test_token"},
            json={"jsonrpc": "2.0", "method": "tools/list", "id": 1}
        )

        assert response.status_code == 200
        result = response.json()
        assert "result" in result
        assert "tools" in result["result"]

    def test_mcp_tools_call(self, authenticated_client):
        """
        EXPECTED: tools/call executes a tool and returns result.
        FAILS UNTIL: Tool execution over HTTP is implemented.
        """
        response = authenticated_client.post(
            "/mcp",
            headers={"Authorization": "Bearer test_token"},
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_my_profile",
                    "arguments": {}
                },
                "id": 1
            }
        )

        assert response.status_code == 200
        result = response.json()
        # tools/call may return result or error (error if Odoo client not initialized)
        assert "result" in result or "error" in result

    def test_mcp_resources_list(self, authenticated_client):
        """
        EXPECTED: resources/list returns available MCP resources.
        FAILS UNTIL: Resource listing over HTTP is implemented.
        """
        response = authenticated_client.post(
            "/mcp",
            headers={"Authorization": "Bearer test_token"},
            json={"jsonrpc": "2.0", "method": "resources/list", "id": 1}
        )

        assert response.status_code == 200
        result = response.json()
        assert "result" in result


class TestHTTPServerConfiguration:
    """Tests for HTTP server configuration."""

    def test_server_uses_config_host(self):
        """
        EXPECTED: Server binds to HTTP_HOST from config.
        """
        from odoo_mcp_server.config import Settings

        settings = Settings()
        assert settings.http_host == "0.0.0.0"

    def test_server_uses_config_port(self):
        """
        EXPECTED: Server binds to HTTP_PORT from config.
        """
        from odoo_mcp_server.config import Settings

        settings = Settings()
        assert settings.http_port == 8080

    def test_server_cors_configuration(self):
        """
        EXPECTED: Server should have CORS configured for browser access.
        FAILS UNTIL: CORS middleware is added.
        """
        from odoo_mcp_server.http_server import app

        # Check if CORS middleware is configured
        cors_middleware = None
        for middleware in app.user_middleware:
            if "CORSMiddleware" in str(middleware):
                cors_middleware = middleware
                break

        assert cors_middleware is not None


class TestOAuthProxyCallback:
    """Tests for the OAuth proxy callback flow (state-based redirect)."""

    @pytest.fixture
    def client(self, monkeypatch):
        """Create test client with OAuth redirect configured."""
        import importlib

        from fastapi.testclient import TestClient

        monkeypatch.setenv("ODOO_URL", "http://localhost:8069")
        monkeypatch.setenv("ODOO_DB", "test_db")
        monkeypatch.setenv("OAUTH_DEV_MODE", "true")
        monkeypatch.setenv("OAUTH_REDIRECT_URI", "https://mcp-server.example.com/callback")
        monkeypatch.setenv("OAUTH_RESOURCE_IDENTIFIER", "https://mcp-server.example.com")
        monkeypatch.setenv("OAUTH_CLIENT_ID", "test-client-id")

        import odoo_mcp_server.http_server as http_server_module

        importlib.reload(http_server_module)
        return TestClient(http_server_module.app)

    def test_authorize_redirects_to_google_with_server_callback(self, client):
        """GET /authorize should redirect to Google using server's own redirect_uri."""
        response = client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": "some-mcp-client",
                "redirect_uri": "http://127.0.0.1:12345/callback",
                "scope": "openid",
                "state": "test-state-123",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        location = response.headers["location"]
        # Should redirect to Google, NOT to 127.0.0.1
        assert "accounts.google.com" in location
        # Should use server's callback, not client's
        assert "redirect_uri=https%3A%2F%2Fmcp-server.example.com%2Fcallback" in location
        # Client's redirect_uri should NOT appear in Google URL
        assert "127.0.0.1" not in location

    def test_authorize_stores_client_redirect_uri(self, client, monkeypatch):
        """GET /authorize should store the client's redirect_uri for later."""
        import odoo_mcp_server.http_server as http_server_module

        # Clear any existing sessions
        http_server_module._pending_auth_sessions.clear()

        client.get(
            "/authorize",
            params={
                "redirect_uri": "http://127.0.0.1:9999/callback",
                "state": "store-test-state",
            },
            follow_redirects=False,
        )

        # Session should be stored
        assert "store-test-state" in http_server_module._pending_auth_sessions
        session = http_server_module._pending_auth_sessions["store-test-state"]
        assert session["client_redirect_uri"] == "http://127.0.0.1:9999/callback"

    def test_callback_redirects_to_client(self, client):
        """GET /callback should redirect to client's stored redirect_uri."""
        import odoo_mcp_server.http_server as http_server_module

        # Pre-store a session
        http_server_module._store_auth_session(
            state="test-state-abc",
            client_redirect_uri="http://127.0.0.1:9999/callback",
        )

        response = client.get(
            "/callback",
            params={"code": "google-auth-code", "state": "test-state-abc"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        location = response.headers["location"]
        assert location.startswith("http://127.0.0.1:9999/callback")
        assert "code=google-auth-code" in location
        assert "state=test-state-abc" in location

    def test_callback_url_encodes_code_with_special_chars(self, client):
        """GET /callback should URL-encode the code param (Google codes contain /)."""
        import odoo_mcp_server.http_server as http_server_module

        http_server_module._store_auth_session(
            state="test-state-enc",
            client_redirect_uri="http://127.0.0.1:9999/callback",
        )

        # Google auth codes contain / (e.g. "4/0AfrIep...")
        response = client.get(
            "/callback",
            params={"code": "4/0AfrIepArSV_test", "state": "test-state-enc"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        location = response.headers["location"]
        # The / in the code must be URL-encoded as %2F
        assert "code=4%2F0AfrIepArSV_test" in location
        assert "state=test-state-enc" in location

    def test_callback_without_session_falls_back_to_postmessage(self, client):
        """GET /callback without pending session should use postMessage fallback."""
        response = client.get(
            "/callback",
            params={"code": "some-code", "state": "unknown-state"},
        )
        assert response.status_code == 200
        assert "postMessage" in response.text

    def test_callback_session_is_one_time_use(self, client):
        """Pending session should be consumed after first use."""
        import odoo_mcp_server.http_server as http_server_module

        http_server_module._store_auth_session(
            state="one-time-state",
            client_redirect_uri="http://127.0.0.1:8888/callback",
        )

        # First call: should redirect
        response1 = client.get(
            "/callback",
            params={"code": "code1", "state": "one-time-state"},
            follow_redirects=False,
        )
        assert response1.status_code == 302

        # Second call: session consumed, should fallback
        response2 = client.get(
            "/callback",
            params={"code": "code2", "state": "one-time-state"},
        )
        assert response2.status_code == 200
        assert "postMessage" in response2.text

    def test_callback_expired_session_falls_back(self, client):
        """Expired sessions should not be used."""
        import odoo_mcp_server.http_server as http_server_module

        # Store session with past timestamp
        http_server_module._pending_auth_sessions["expired-state"] = {
            "client_redirect_uri": "http://127.0.0.1:7777/callback",
            "created_at": 0,  # epoch = expired
        }

        response = client.get(
            "/callback",
            params={"code": "code", "state": "expired-state"},
        )
        assert response.status_code == 200
        assert "postMessage" in response.text

    def test_callback_error_redirects_to_client(self, client):
        """OAuth errors should be forwarded to client's redirect_uri."""
        import odoo_mcp_server.http_server as http_server_module

        http_server_module._store_auth_session(
            state="error-state",
            client_redirect_uri="http://127.0.0.1:6666/callback",
        )

        response = client.get(
            "/callback",
            params={"error": "access_denied", "state": "error-state"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        location = response.headers["location"]
        assert "error=access_denied" in location
        assert "state=error-state" in location

    def test_callback_missing_code_returns_html_error(self, client):
        """GET /callback without code should return HTML error."""
        response = client.get(
            "/callback",
            params={"state": "some-state"},
        )
        assert response.status_code == 400
        assert "Missing authorization code" in response.text


class TestRefreshTokenGrant:
    """Tests for refresh_token grant type."""

    @pytest.fixture
    def client(self, monkeypatch):
        import importlib

        from fastapi.testclient import TestClient

        monkeypatch.setenv("ODOO_URL", "http://localhost:8069")
        monkeypatch.setenv("ODOO_DB", "test_db")
        monkeypatch.setenv("OAUTH_DEV_MODE", "true")
        monkeypatch.setenv("OAUTH_REDIRECT_URI", "https://mcp-server.example.com/callback")
        monkeypatch.setenv("OAUTH_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("OAUTH_CLIENT_SECRET", "test-secret")

        import odoo_mcp_server.http_server as http_server_module

        importlib.reload(http_server_module)
        return TestClient(http_server_module.app)

    def test_refresh_token_missing_returns_400(self, client):
        """POST /token with grant_type=refresh_token but no token returns 400."""
        response = client.post(
            "/token",
            data={"grant_type": "refresh_token"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_request"

    def test_authorization_server_metadata_includes_refresh_token(self, client):
        """Metadata should advertise refresh_token grant type."""
        response = client.get("/.well-known/oauth-authorization-server")
        assert response.status_code == 200
        metadata = response.json()
        assert "refresh_token" in metadata["grant_types_supported"]

    def test_unsupported_grant_type_returns_400(self, client):
        """POST /token with unsupported grant_type returns 400."""
        response = client.post(
            "/token",
            data={"grant_type": "client_credentials"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "unsupported_grant_type"


class TestRedirectURIValidation:
    """Tests for redirect URI validation in /authorize."""

    @pytest.fixture
    def client(self, monkeypatch):
        import importlib

        from fastapi.testclient import TestClient

        monkeypatch.setenv("ODOO_URL", "http://localhost:8069")
        monkeypatch.setenv("ODOO_DB", "test_db")
        monkeypatch.setenv("OAUTH_DEV_MODE", "true")
        monkeypatch.setenv("OAUTH_REDIRECT_URI", "https://mcp-server.example.com/callback")
        monkeypatch.setenv("OAUTH_RESOURCE_IDENTIFIER", "https://mcp-server.example.com")
        monkeypatch.setenv("OAUTH_CLIENT_ID", "test-client-id")

        import odoo_mcp_server.http_server as http_server_module
        importlib.reload(http_server_module)
        return TestClient(http_server_module.app)

    def test_reject_evil_redirect_uri(self, client):
        """Malicious redirect_uri should be rejected with 400."""
        response = client.get(
            "/authorize",
            params={
                "redirect_uri": "https://evil.com/steal",
                "state": "test",
            },
            follow_redirects=False,
        )
        assert response.status_code == 400
        assert "invalid" in response.json().get("error", "").lower() or "invalid_request" == response.json().get("error")

    def test_accept_localhost_redirect(self, client):
        """localhost redirect_uri should be accepted (CLI clients)."""
        response = client.get(
            "/authorize",
            params={
                "redirect_uri": "http://localhost:12345/callback",
                "state": "test-local",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

    def test_accept_127001_redirect(self, client):
        """127.0.0.1 redirect_uri should be accepted (CLI clients)."""
        response = client.get(
            "/authorize",
            params={
                "redirect_uri": "http://127.0.0.1:9999/callback",
                "state": "test-127",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

    def test_accept_claude_ai_redirect(self, client):
        """claude.ai redirect_uri should be accepted."""
        response = client.get(
            "/authorize",
            params={
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "state": "test-claude",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

    def test_reject_http_non_localhost(self, client):
        """http:// to non-localhost should be rejected."""
        response = client.get(
            "/authorize",
            params={
                "redirect_uri": "http://evil.com/steal",
                "state": "test",
            },
            follow_redirects=False,
        )
        assert response.status_code == 400

    def test_default_redirect_uri_when_none(self, client):
        """No redirect_uri should default to claude.ai (which is allowed)."""
        response = client.get(
            "/authorize",
            params={"state": "test-default"},
            follow_redirects=False,
        )
        assert response.status_code == 302


class TestPostMessageOrigin:
    """Tests for postMessage origin security."""

    @pytest.fixture
    def client(self, monkeypatch):
        import importlib

        from fastapi.testclient import TestClient

        monkeypatch.setenv("ODOO_URL", "http://localhost:8069")
        monkeypatch.setenv("ODOO_DB", "test_db")
        monkeypatch.setenv("OAUTH_DEV_MODE", "true")

        import odoo_mcp_server.http_server as http_server_module
        importlib.reload(http_server_module)
        return TestClient(http_server_module.app)

    def test_postmessage_uses_specific_origin(self, client):
        """postMessage fallback should use https://claude.ai, not wildcard *."""
        response = client.get(
            "/callback",
            params={"code": "test-code", "state": "unknown-state"},
        )
        assert response.status_code == 200
        assert "'https://claude.ai'" in response.text
        assert "'*'" not in response.text


class TestMCPProtocolCompliance:
    """Tests for MCP 2025-03-26 Streamable HTTP protocol compliance."""

    @pytest.fixture
    def client(self, monkeypatch):
        import importlib

        from fastapi.testclient import TestClient

        monkeypatch.setenv("ODOO_URL", "http://localhost:8069")
        monkeypatch.setenv("ODOO_DB", "test_db")
        monkeypatch.setenv("OAUTH_DEV_MODE", "true")

        import odoo_mcp_server.http_server as http_server_module
        importlib.reload(http_server_module)
        return TestClient(http_server_module.app)

    def test_initialize_returns_session_id_header(self, client):
        """initialize should return Mcp-Session-Id header."""
        response = client.post(
            "/mcp",
            headers={"Authorization": "Bearer test"},
            json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
        )
        assert response.status_code == 200
        assert "Mcp-Session-Id" in response.headers
        assert len(response.headers["Mcp-Session-Id"]) == 36  # UUID format

    def test_initialize_returns_2025_protocol_version(self, client):
        """initialize should return protocolVersion 2025-03-26."""
        response = client.post(
            "/mcp",
            headers={"Authorization": "Bearer test"},
            json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
        )
        result = response.json()["result"]
        assert result["protocolVersion"] == "2025-03-26"

    def test_notification_returns_202(self, client):
        """Notifications should return 202 Accepted (not 200)."""
        response = client.post(
            "/mcp",
            headers={"Authorization": "Bearer test"},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        assert response.status_code == 202

    def test_delete_mcp_returns_200(self, client):
        """DELETE /mcp should return 200."""
        response = client.delete(
            "/mcp",
            headers={"Authorization": "Bearer test"},
        )
        assert response.status_code == 200

    def test_unknown_method_returns_error_32601(self, client):
        """Unknown method should return error code -32601."""
        response = client.post(
            "/mcp",
            headers={"Authorization": "Bearer test"},
            json={"jsonrpc": "2.0", "method": "nonexistent/method", "id": 1},
        )
        body = response.json()
        assert "error" in body
        assert body["error"]["code"] == -32601

    def test_success_response_has_no_error_field(self, client):
        """JSON-RPC success response MUST NOT have error field."""
        response = client.post(
            "/mcp",
            headers={"Authorization": "Bearer test"},
            json={"jsonrpc": "2.0", "method": "ping", "id": 1},
        )
        body = response.json()
        assert "result" in body
        assert "error" not in body

    def test_error_response_has_no_result_field(self, client):
        """JSON-RPC error response MUST NOT have result field."""
        response = client.post(
            "/mcp",
            headers={"Authorization": "Bearer test"},
            json={"jsonrpc": "2.0", "method": "nonexistent/method", "id": 1},
        )
        body = response.json()
        assert "error" in body
        assert "result" not in body

    def test_jsonrpc_id_propagation(self, client):
        """Response should echo back the request ID."""
        response = client.post(
            "/mcp",
            headers={"Authorization": "Bearer test"},
            json={"jsonrpc": "2.0", "method": "ping", "id": 42},
        )
        assert response.json()["id"] == 42


class TestToolGroupAndReadOnlyModes:
    """Versatility: tool-group selection and read-only YOLO mode over HTTP."""

    def _client(self, monkeypatch, **env):
        import importlib

        from fastapi.testclient import TestClient

        monkeypatch.setenv("ODOO_URL", "http://localhost:8069")
        monkeypatch.setenv("ODOO_DB", "test_db")
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        import odoo_mcp_server.http_server as http_server_module

        importlib.reload(http_server_module)
        return TestClient(http_server_module.app)

    def _tool_names(self, client):
        response = client.post(
            "/mcp",
            headers={"Authorization": "Bearer test_token"},
            json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
        )
        assert response.status_code == 200
        return {t["name"] for t in response.json()["result"]["tools"]}

    def test_crud_only_group(self, monkeypatch):
        """ENABLED_TOOL_GROUPS=crud exposes CRUD tools but not employee tools."""
        client = self._client(monkeypatch, OAUTH_DEV_MODE="true", ENABLED_TOOL_GROUPS="crud")
        names = self._tool_names(client)
        assert "search_records" in names
        assert "get_my_profile" not in names

    def test_employee_only_group_hides_crud(self, monkeypatch):
        """ENABLED_TOOL_GROUPS=employee hides CRUD tools, and calling one 404s."""
        client = self._client(monkeypatch, OAUTH_DEV_MODE="true", ENABLED_TOOL_GROUPS="employee")
        names = self._tool_names(client)
        assert "get_my_profile" in names
        assert "search_records" not in names

        response = client.post(
            "/mcp",
            headers={"Authorization": "Bearer test_token"},
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "search_records", "arguments": {}},
                "id": 1,
            },
        )
        body = response.json()
        assert "error" in body

    def test_yolo_read_mode_hides_write_tools(self, monkeypatch):
        """YOLO_MODE=read grants read-only scopes, so write tools are not listed."""
        client = self._client(monkeypatch, YOLO_MODE="read")
        names = self._tool_names(client)
        assert "search_records" in names
        assert "get_my_profile" in names
        # Write tools require write scopes that read-only mode does not grant.
        assert "create_record" not in names
        assert "request_leave" not in names
        assert "update_my_contact" not in names

    def test_yolo_full_mode_includes_write_tools(self, monkeypatch):
        """YOLO_MODE=true keeps full scopes including write tools."""
        client = self._client(monkeypatch, YOLO_MODE="true")
        names = self._tool_names(client)
        assert "create_record" in names
