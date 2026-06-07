# odoo_mcp_bridge — per-user identity mode

A standalone FastMCP + Starlette app that authenticates the caller via Google OAuth and
performs Odoo operations **as that user** — using a per-user Odoo API key it auto-mints
and stores encrypted — so Odoo's native record rules apply and `create_uid`/`write_uid`
reflect the real person. The existing `odoo_mcp_server.http_server` (shared
service-account mode) is unchanged; this is a separate, opt-in entrypoint.

```bash
pip install ".[bridge]"        # + ".[bridge-gcp]" for the Secret Manager vault
odoo-mcp-bridge                # or: python -m odoo_mcp_bridge
```

## Dependencies

### 1. Companion Odoo addon (REQUIRED)
This bridge has a **hard dependency** on the Odoo addon **`mcp_apikey_provisioning`**
(shipped in [`odoo_addons/mcp_apikey_provisioning/`](../../odoo_addons/mcp_apikey_provisioning/)).
It adds the admin-only `res.users.mcp_mint_apikey` method the bridge calls to mint
per-user keys. It must be **installed on the Odoo server** (deployed with the service
account). Without it, the bridge still starts and serves `/health`, logs a preflight
warning, and per-user Odoo calls fail with a clear message
(see `provisioning.REQUIRED_ODOO_ADDON`). Deployment steps:
[`docs/odoo-team-instructions.md`](../../docs/odoo-team-instructions.md).

### 2. Service account (config-driven, not hardcoded)
The bridge mints keys using an Odoo **System Administrator** service account
(e.g. `svc-mcp@example.com`), supplied **only** via the `ODOO_USERNAME` /
`ODOO_API_KEY` environment variables — never hardcoded.

### 3. Python extras
`[bridge]` → `fastmcp`, `cryptography`. `[bridge-gcp]` → `google-cloud-secret-manager`
(only for the Secret Manager vault backend).

## Configuration

All via environment (see [`.env.example`](../../.env.example)); required values fail fast
at startup. Key vars: `ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`, `ODOO_API_KEY`,
`OAUTH_CLIENT_ID`, `OAUTH_CLIENT_SECRET`, `OAUTH_RESOURCE_IDENTIFIER`, `SESSION_SECRET`,
`BRIDGE_ALLOWED_EMAILS`/`BRIDGE_ALLOWED_DOMAINS`, `TOKEN_STORAGE_BACKEND`
(`memory`|`encrypted_file`|`gcp_secret_manager`), `TOKEN_ENCRYPTION_KEY`,
`ODOO_KEY_TTL_DAYS`.

## How it works

1. `OdooVaultGoogleProvider.verify_token` validates the Google token → email.
2. It looks up the user's Odoo API key in the vault; on a miss it auto-mints one via the
   companion addon (`provisioning.mint_user_key`) and stores it encrypted.
3. It returns an `AccessToken` whose token is the user's Odoo key
   (`auth_method=odoo_api_key`); `tools_adapter.register_odoo_tools` exposes every existing
   Odoo tool (CRUD + employee + optional sign, per `ENABLED_TOOL_GROUPS`/`SIGN_MODULE_ENABLED`)
   as a FastMCP tool that builds a per-user `OdooClient` and runs the existing tool logic
   as the calling user.

> End-to-end verification against a live Odoo needs the `mcp_apikey_provisioning` addon
> installed and the service account in its group.
