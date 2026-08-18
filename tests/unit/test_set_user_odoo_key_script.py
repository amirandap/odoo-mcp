"""Tests for the scripts/set_user_odoo_key.py admin CLI."""
import importlib.util
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

pytestmark = [pytest.mark.unit]

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "set_user_odoo_key.py"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("set_user_odoo_key", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def vault_args(tmp_path):
    return ["--path", str(tmp_path / "keys.json"), "--key", Fernet.generate_key().decode()]


class TestSetUserOdooKeyScript:
    def test_set_then_show_reports_linked_login(self, script, vault_args, capsys):
        rc = script.main([*vault_args, "set", "yosimar@example.com", "yosimar@vivaldi.odoo", "her-own-key"])
        assert rc == 0
        capsys.readouterr()

        rc = script.main([*vault_args, "show", "yosimar@example.com"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "yosimar@vivaldi.odoo" in out
        assert "her-own-key" not in out

    def test_show_without_entry_reports_none(self, script, vault_args, capsys):
        rc = script.main([*vault_args, "show", "nobody@example.com"])
        assert rc == 0
        assert "no personal Odoo credential" in capsys.readouterr().out

    def test_set_then_delete_then_show_reports_none(self, script, vault_args, capsys):
        script.main([*vault_args, "set", "yosimar@example.com", "yosimar@vivaldi.odoo", "her-own-key"])
        rc = script.main([*vault_args, "delete", "yosimar@example.com"])
        assert rc == 0
        capsys.readouterr()

        script.main([*vault_args, "show", "yosimar@example.com"])
        assert "no personal Odoo credential" in capsys.readouterr().out

    def test_missing_path_and_key_fails_clearly(self, script, capsys, monkeypatch):
        monkeypatch.delenv("USER_KEY_VAULT_PATH", raising=False)
        monkeypatch.delenv("USER_KEY_VAULT_ENCRYPTION_KEY", raising=False)

        rc = script.main(["show", "yosimar@example.com"])

        assert rc == 1
        assert "USER_KEY_VAULT_PATH" in capsys.readouterr().err

    def test_reads_path_and_key_from_environment_by_default(self, script, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("USER_KEY_VAULT_PATH", str(tmp_path / "keys.json"))
        monkeypatch.setenv("USER_KEY_VAULT_ENCRYPTION_KEY", Fernet.generate_key().decode())

        rc = script.main(["set", "yosimar@example.com", "yosimar@vivaldi.odoo", "her-own-key"])

        assert rc == 0
