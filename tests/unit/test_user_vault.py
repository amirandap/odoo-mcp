"""Tests for the per-user Odoo credential vault.

Background: CRUD_ADMIN_EMAILS grants a fixed set of emails full, unfiltered
odoo.read/odoo.write access via the shared service account. For a user who
should be limited to whatever their OWN Odoo account can do (native
ir.model.access / record rules), we instead store their personal
(odoo_login, odoo_api_key) here, keyed by their OAuth email. When present,
generic CRUD tools authenticate to Odoo as that user directly, bypassing the
admin allowlist for their calls.
"""
import json

import pytest
from cryptography.fernet import Fernet

from odoo_mcp_server.odoo.user_vault import UserCredentialVault, build_user_vault

pytestmark = [pytest.mark.unit]


@pytest.fixture
def encryption_key() -> str:
    return Fernet.generate_key().decode()


class TestUserCredentialVault:
    def test_missing_entry_returns_none(self, tmp_path, encryption_key):
        vault = UserCredentialVault(str(tmp_path / "keys.json"), encryption_key)
        assert vault.get_credentials("nobody@example.com") is None

    def test_round_trip(self, tmp_path, encryption_key):
        path = str(tmp_path / "keys.json")
        vault = UserCredentialVault(path, encryption_key)
        vault.put_credentials("yosimar@example.com", "yosimar@vivaldi.odoo", "her-own-key")

        # New instance re-reading the same file (mirrors a container restart).
        reopened = UserCredentialVault(path, encryption_key)
        creds = reopened.get_credentials("yosimar@example.com")

        assert creds == ("yosimar@vivaldi.odoo", "her-own-key")

    def test_email_lookup_is_case_and_whitespace_insensitive(self, tmp_path, encryption_key):
        vault = UserCredentialVault(str(tmp_path / "keys.json"), encryption_key)
        vault.put_credentials("Yosimar@Example.com", "yosimar@vivaldi.odoo", "her-own-key")
        assert vault.get_credentials(" yosimar@example.com ") == ("yosimar@vivaldi.odoo", "her-own-key")

    def test_secret_not_present_in_plaintext_on_disk(self, tmp_path, encryption_key):
        path = tmp_path / "keys.json"
        vault = UserCredentialVault(str(path), encryption_key)
        vault.put_credentials("yosimar@example.com", "yosimar@vivaldi.odoo", "her-own-key")

        raw = path.read_text("utf-8")
        assert "her-own-key" not in raw
        assert "yosimar@vivaldi.odoo" not in raw
        assert "yosimar@example.com" not in raw

    def test_delete_removes_entry(self, tmp_path, encryption_key):
        vault = UserCredentialVault(str(tmp_path / "keys.json"), encryption_key)
        vault.put_credentials("yosimar@example.com", "yosimar@vivaldi.odoo", "her-own-key")
        vault.delete_credentials("yosimar@example.com")
        assert vault.get_credentials("yosimar@example.com") is None

    def test_wrong_encryption_key_cannot_decrypt(self, tmp_path, encryption_key):
        path = str(tmp_path / "keys.json")
        UserCredentialVault(path, encryption_key).put_credentials(
            "yosimar@example.com", "yosimar@vivaldi.odoo", "her-own-key"
        )
        other = UserCredentialVault(path, Fernet.generate_key().decode())
        assert other.get_credentials("yosimar@example.com") is None

    def test_malformed_payload_returns_none_instead_of_raising(self, tmp_path, encryption_key):
        """A stray/foreign entry in the same encrypted file (not a JSON {login, api_key}
        payload) must not crash the lookup - just be treated as absent."""
        from odoo_mcp_bridge.vault import EncryptedFileVault

        path = str(tmp_path / "keys.json")
        # Write via the raw bridge vault (single opaque string, not our JSON envelope).
        EncryptedFileVault(path, encryption_key).put_key("odd@example.com", "not-json")
        vault = UserCredentialVault(path, encryption_key)
        assert vault.get_credentials("odd@example.com") is None

    def test_missing_login_or_key_in_payload_returns_none(self, tmp_path, encryption_key):
        from odoo_mcp_bridge.vault import EncryptedFileVault

        path = str(tmp_path / "keys.json")
        EncryptedFileVault(path, encryption_key).put_key(
            "partial@example.com", json.dumps({"odoo_login": "partial@vivaldi.odoo"})
        )
        vault = UserCredentialVault(path, encryption_key)
        assert vault.get_credentials("partial@example.com") is None


class TestBuildUserVault:
    def test_returns_none_when_unconfigured(self):
        from types import SimpleNamespace

        settings = SimpleNamespace(user_key_vault_path=None, user_key_vault_encryption_key=None)
        assert build_user_vault(settings) is None

    def test_returns_none_when_path_set_but_key_missing(self, tmp_path):
        from types import SimpleNamespace

        settings = SimpleNamespace(
            user_key_vault_path=str(tmp_path / "keys.json"), user_key_vault_encryption_key=None
        )
        assert build_user_vault(settings) is None

    def test_returns_vault_when_fully_configured(self, tmp_path, encryption_key):
        from types import SimpleNamespace

        settings = SimpleNamespace(
            user_key_vault_path=str(tmp_path / "keys.json"),
            user_key_vault_encryption_key=encryption_key,
        )
        vault = build_user_vault(settings)
        assert isinstance(vault, UserCredentialVault)
