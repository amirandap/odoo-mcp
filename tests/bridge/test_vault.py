"""Unit tests for the per-user Odoo API-key vault."""

import json

import pytest

from odoo_mcp_bridge.vault import (
    EncryptedFileVault,
    InMemoryVault,
    secret_id_for_email,
)

pytestmark = [pytest.mark.unit]


def test_secret_id_is_charset_safe_and_stable():
    sid = secret_id_for_email("Alice@Example.com ")
    assert sid.startswith("odoo-key-")
    assert all(c.isalnum() or c in "-_" for c in sid)
    # Case/whitespace-insensitive (email is normalized).
    assert sid == secret_id_for_email("alice@example.com")


class TestInMemoryVault:
    def test_round_trip(self):
        v = InMemoryVault()
        assert v.get_key("a@x.com") is None
        v.put_key("a@x.com", "key-123")
        assert v.get_key("a@x.com") == "key-123"

    def test_normalization(self):
        v = InMemoryVault()
        v.put_key("A@X.com", "k")
        assert v.get_key("a@x.com ") == "k"

    def test_delete(self):
        v = InMemoryVault()
        v.put_key("a@x.com", "k")
        v.delete_key("a@x.com")
        assert v.get_key("a@x.com") is None

    def test_get_key_max_age_triggers_rotation(self):
        import time

        v = InMemoryVault()
        v.put_key("a@x.com", "k")
        assert v.get_key("a@x.com", max_age_seconds=10_000) == "k"  # fresh
        # Age the stored entry past the threshold -> treated as stale (None) so caller re-mints.
        v._store[secret_id_for_email("a@x.com")]["minted_at"] = int(time.time()) - 1000
        assert v.get_key("a@x.com", max_age_seconds=10) is None
        assert v.get_key("a@x.com") == "k"  # no max_age -> still returned


class TestEncryptedFileVault:
    @pytest.fixture
    def key(self):
        from cryptography.fernet import Fernet

        return Fernet.generate_key().decode()

    def test_round_trip_and_encrypted_at_rest(self, tmp_path, key):
        path = tmp_path / "keys.json"
        v = EncryptedFileVault(str(path), key)
        v.put_key("bob@x.com", "secret-key-value")
        # New instance (re-reads the file) returns the key.
        v2 = EncryptedFileVault(str(path), key)
        assert v2.get_key("bob@x.com") == "secret-key-value"
        # The plaintext key must NOT appear in the file on disk.
        raw = path.read_text("utf-8")
        assert "secret-key-value" not in raw
        assert "bob@x.com" not in raw  # email is inside the encrypted payload too
        # File is a mapping of id -> fernet token.
        data = json.loads(raw)
        assert list(data.keys()) == [secret_id_for_email("bob@x.com")]

    def test_missing_returns_none(self, tmp_path, key):
        v = EncryptedFileVault(str(tmp_path / "keys.json"), key)
        assert v.get_key("nobody@x.com") is None

    def test_wrong_key_cannot_decrypt(self, tmp_path, key):
        from cryptography.fernet import Fernet

        path = tmp_path / "keys.json"
        EncryptedFileVault(str(path), key).put_key("bob@x.com", "k")
        other = EncryptedFileVault(str(path), Fernet.generate_key().decode())
        assert other.get_key("bob@x.com") is None  # invalid token -> None, no crash

    def test_delete(self, tmp_path, key):
        path = tmp_path / "keys.json"
        v = EncryptedFileVault(str(path), key)
        v.put_key("bob@x.com", "k")
        v.delete_key("bob@x.com")
        assert v.get_key("bob@x.com") is None


class TestBuildVault:
    def test_memory(self):
        from types import SimpleNamespace

        from odoo_mcp_bridge.vault import InMemoryVault, build_vault

        cfg = SimpleNamespace(storage_backend="memory")
        assert isinstance(build_vault(cfg), InMemoryVault)

    def test_encrypted_file(self, tmp_path):
        from types import SimpleNamespace

        from cryptography.fernet import Fernet

        from odoo_mcp_bridge.vault import EncryptedFileVault, build_vault

        cfg = SimpleNamespace(
            storage_backend="encrypted_file",
            token_store_path=str(tmp_path / "k.json"),
            token_encryption_key=Fernet.generate_key().decode(),
        )
        assert isinstance(build_vault(cfg), EncryptedFileVault)

    def test_unknown_backend(self):
        from types import SimpleNamespace

        from odoo_mcp_bridge.vault import build_vault

        with pytest.raises(RuntimeError):
            build_vault(SimpleNamespace(storage_backend="bogus"))
