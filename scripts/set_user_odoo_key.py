#!/usr/bin/env python3
"""Admin CLI: manage per-user Odoo credentials in the vault (odoo/user_vault.py).

Run this on the server after generating someone's OWN Odoo API key (Odoo:
Settings > Users > their account > Account Security > New API Key). Once
stored, generic CRUD tools authenticate to Odoo as that person, and Odoo's
own permissions on their account decide what they can do - no container
restart needed, the vault file is read fresh on every lookup.

Usage:
    python scripts/set_user_odoo_key.py set alice@example.com alice@acme.odoo <api-key>
    python scripts/set_user_odoo_key.py show alice@example.com
    python scripts/set_user_odoo_key.py delete alice@example.com

Reads USER_KEY_VAULT_PATH / USER_KEY_VAULT_ENCRYPTION_KEY from the environment
(the same .env the server uses) unless overridden with --path/--key.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from odoo_mcp_server.odoo.user_vault import UserCredentialVault  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--path", default=os.environ.get("USER_KEY_VAULT_PATH"), help="Vault file path (default: $USER_KEY_VAULT_PATH)"
    )
    parser.add_argument(
        "--key",
        default=os.environ.get("USER_KEY_VAULT_ENCRYPTION_KEY"),
        help="Fernet encryption key (default: $USER_KEY_VAULT_ENCRYPTION_KEY)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    set_parser = subparsers.add_parser("set", help="Store or replace a user's personal Odoo credential")
    set_parser.add_argument("email", help="OAuth email the user authenticates with")
    set_parser.add_argument("odoo_login", help="The user's own Odoo login")
    set_parser.add_argument("odoo_api_key", help="The user's own Odoo API key")

    delete_parser = subparsers.add_parser("delete", help="Remove a user's personal Odoo credential")
    delete_parser.add_argument("email")

    show_parser = subparsers.add_parser("show", help="Report whether a user has a credential on file (never prints the key)")
    show_parser.add_argument("email")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.path or not args.key:
        print(
            "USER_KEY_VAULT_PATH and USER_KEY_VAULT_ENCRYPTION_KEY must be set "
            "(via environment or --path/--key).",
            file=sys.stderr,
        )
        return 1

    vault = UserCredentialVault(args.path, args.key)

    if args.command == "set":
        vault.put_credentials(args.email, args.odoo_login, args.odoo_api_key)
        print(f"Stored a personal Odoo credential for {args.email} (login: {args.odoo_login}).")
        return 0

    if args.command == "delete":
        vault.delete_credentials(args.email)
        print(f"Removed the personal Odoo credential for {args.email}, if any.")
        return 0

    if args.command == "show":
        credentials = vault.get_credentials(args.email)
        if credentials is None:
            print(f"{args.email}: no personal Odoo credential on file.")
        else:
            login, _ = credentials
            print(f"{args.email}: linked to Odoo login {login}.")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
