"""
Unit tests for configuration module.

Run with: pytest tests/unit/test_config.py -v -m unit
"""

import pytest

pytestmark = [pytest.mark.unit]


@pytest.fixture(autouse=True)
def _set_required_env(monkeypatch):
    """Set required env vars for Settings() unless the test explicitly clears them."""
    monkeypatch.setenv("ODOO_URL", "https://test.example.com")
    monkeypatch.setenv("ODOO_DB", "test_db")


class TestSettingsLoading:
    """Tests for Settings class loading from environment."""

    def test_odoo_url_required(self, monkeypatch):
        """Odoo URL must be provided via environment - no silent default."""
        monkeypatch.delenv("ODOO_URL")

        from pydantic import ValidationError

        from odoo_mcp_server.config import Settings

        with pytest.raises(ValidationError):
            Settings()

    def test_odoo_url_from_env(self, monkeypatch):
        """Odoo URL should be loaded from environment."""
        monkeypatch.setenv("ODOO_URL", "https://custom.odoo.com")

        from odoo_mcp_server.config import Settings

        settings = Settings()
        assert settings.odoo_url == "https://custom.odoo.com"

    def test_odoo_db_required(self, monkeypatch):
        """Odoo database must be provided via environment - no silent default."""
        monkeypatch.delenv("ODOO_DB")

        from pydantic import ValidationError

        from odoo_mcp_server.config import Settings

        with pytest.raises(ValidationError):
            Settings()

    def test_odoo_db_from_env(self, monkeypatch):
        """Odoo database should be loaded from environment."""
        monkeypatch.setenv("ODOO_URL", "https://custom.odoo.com")
        monkeypatch.setenv("ODOO_DB", "custom_db")

        from odoo_mcp_server.config import Settings

        settings = Settings()
        assert settings.odoo_db == "custom_db"

    def test_api_key_from_env(self, monkeypatch):
        """API key should be loaded from environment."""
        monkeypatch.setenv("ODOO_API_KEY", "secret_key_123")

        from odoo_mcp_server.config import Settings

        settings = Settings()
        assert settings.odoo_api_key == "secret_key_123"

    def test_api_key_none_by_default(self, monkeypatch):
        """API key should be None if not set."""
        monkeypatch.delenv("ODOO_API_KEY", raising=False)

        from odoo_mcp_server.config import Settings

        settings = Settings()
        assert settings.odoo_api_key is None


class TestOAuthSettings:
    """Tests for OAuth configuration settings."""

    def test_default_authorization_server(self, monkeypatch):
        """Default authorization server should be Google OAuth (default provider)."""
        monkeypatch.delenv("OAUTH_AUTHORIZATION_SERVER", raising=False)

        from odoo_mcp_server.config import Settings

        settings = Settings()
        # Default is Google OAuth
        assert settings.oauth_authorization_server == "https://accounts.google.com"

    def test_authorization_server_from_env(self, monkeypatch):
        """Authorization server should be loaded from environment."""
        monkeypatch.setenv("OAUTH_AUTHORIZATION_SERVER", "https://auth0.example.com")

        from odoo_mcp_server.config import Settings

        settings = Settings()
        assert settings.oauth_authorization_server == "https://auth0.example.com"

    def test_resource_identifier_none_by_default(self, monkeypatch):
        """Resource identifier should be None if not set."""
        monkeypatch.setenv("ODOO_URL", "https://test.example.com")
        monkeypatch.setenv("ODOO_DB", "test_db")
        monkeypatch.delenv("OAUTH_RESOURCE_IDENTIFIER", raising=False)

        from odoo_mcp_server.config import Settings

        settings = Settings()
        assert settings.oauth_resource_identifier is None

    def test_effective_issuer_defaults_to_auth_server(self, monkeypatch):
        """Effective issuer should use oauth_issuer when set, falling back to auth server."""
        # When oauth_issuer is set explicitly, it takes precedence
        monkeypatch.setenv("OAUTH_ISSUER", "https://auth.example.com")
        monkeypatch.setenv("OAUTH_AUTHORIZATION_SERVER", "https://different.example.com")

        from odoo_mcp_server.config import Settings

        settings = Settings()
        # effective_issuer returns oauth_issuer (which is set)
        assert settings.effective_issuer == "https://auth.example.com"

    def test_effective_issuer_uses_explicit_value(self, monkeypatch):
        """Effective issuer should use explicit value when set."""
        monkeypatch.setenv("OAUTH_ISSUER", "https://issuer.example.com")
        monkeypatch.setenv("OAUTH_AUTHORIZATION_SERVER", "https://auth.example.com")

        from odoo_mcp_server.config import Settings

        settings = Settings()
        assert settings.effective_issuer == "https://issuer.example.com"


class TestHTTPServerSettings:
    """Tests for HTTP server configuration settings."""

    def test_default_http_host(self, monkeypatch):
        """Default HTTP host should be 0.0.0.0."""
        monkeypatch.delenv("HTTP_HOST", raising=False)

        from odoo_mcp_server.config import Settings

        settings = Settings()
        assert settings.http_host == "0.0.0.0"

    def test_default_http_port(self, monkeypatch):
        """Default HTTP port should be 8080."""
        monkeypatch.delenv("HTTP_PORT", raising=False)

        from odoo_mcp_server.config import Settings

        settings = Settings()
        assert settings.http_port == 8080

    def test_http_port_from_env(self, monkeypatch):
        """HTTP port should be loaded from environment."""
        monkeypatch.setenv("HTTP_PORT", "9000")

        from odoo_mcp_server.config import Settings

        settings = Settings()
        assert settings.http_port == 9000


class TestDevelopmentSettings:
    """Tests for development configuration settings."""

    def test_debug_false_by_default(self, monkeypatch):
        """Debug should be False by default."""
        monkeypatch.delenv("DEBUG", raising=False)

        from odoo_mcp_server.config import Settings

        settings = Settings()
        assert settings.debug is False

    def test_debug_from_env(self, monkeypatch):
        """Debug should be loaded from environment."""
        monkeypatch.setenv("DEBUG", "true")

        from odoo_mcp_server.config import Settings

        settings = Settings()
        assert settings.debug is True

    def test_yolo_mode_none_by_default(self, monkeypatch):
        """YOLO mode should be None by default."""
        monkeypatch.delenv("YOLO_MODE", raising=False)

        from odoo_mcp_server.config import Settings

        settings = Settings()
        assert settings.yolo_mode is None

    def test_yolo_mode_read(self, monkeypatch):
        """YOLO mode can be set to 'read'."""
        monkeypatch.setenv("YOLO_MODE", "read")

        from odoo_mcp_server.config import Settings

        settings = Settings()
        assert settings.yolo_mode == "read"


class TestReadOnlyScopes:
    """Tests for the read-only scope derivation."""

    def test_read_only_scopes_excludes_writes(self):
        """read_only_scopes() must exclude every write-bearing scope."""
        from odoo_mcp_server.config import (
            OAUTH_SCOPES,
            WRITE_SCOPES,
            read_only_scopes,
        )

        ro = set(read_only_scopes())
        assert ro.isdisjoint(WRITE_SCOPES)
        assert "odoo.read" in ro
        assert ro == set(OAUTH_SCOPES) - WRITE_SCOPES


class TestToolGroupSettings:
    """Tests for ENABLED_TOOL_GROUPS / effective_tool_groups."""

    def test_effective_tool_groups_default(self, monkeypatch):
        """Default exposes crud + employee, no sign."""
        monkeypatch.delenv("ENABLED_TOOL_GROUPS", raising=False)
        monkeypatch.delenv("SIGN_MODULE_ENABLED", raising=False)

        from odoo_mcp_server.config import Settings

        assert Settings().effective_tool_groups == {"crud", "employee"}

    def test_effective_tool_groups_crud_only(self, monkeypatch):
        """ENABLED_TOOL_GROUPS can restrict to a single group."""
        monkeypatch.setenv("ENABLED_TOOL_GROUPS", "crud")
        monkeypatch.delenv("SIGN_MODULE_ENABLED", raising=False)

        from odoo_mcp_server.config import Settings

        assert Settings().effective_tool_groups == {"crud"}

    def test_effective_tool_groups_adds_sign_when_enabled(self, monkeypatch):
        """sign_module_enabled adds 'sign' to the effective groups."""
        monkeypatch.setenv("ENABLED_TOOL_GROUPS", "employee")
        monkeypatch.setenv("SIGN_MODULE_ENABLED", "true")

        from odoo_mcp_server.config import Settings

        assert Settings().effective_tool_groups == {"employee", "sign"}
