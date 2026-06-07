# mcp_apikey_provisioning

Companion Odoo addon for the MCP server's **per-user identity mode**. It lets the MCP
server (authenticated as a trusted admin/service account) mint an `rpc`-scoped API key
**on behalf of an end user**, so subsequent Odoo calls run as that user — applying
Odoo's native record rules and recording the real user as `create_uid`/`write_uid`,
instead of everything running as the shared service account.

## Why it's needed

Stock Odoo 18 has **no way to mint an API key for another user over XML-RPC**:
`res.users.apikeys.generate` is not exposed as an RPC method, `_generate` is private,
and there is no admin "mint for user" method. (Verified against staging Odoo 18.0.)
This addon adds one admin-guarded method to close that gap.

## What it adds

`res.users.mcp_mint_apikey(user_id, name=None, ttl_days=30) -> str`
- Gated on the group `mcp_apikey_provisioning.group_mcp_provisioning`; raises
  `AccessError` otherwise.
- Mints a fresh `rpc`-scoped key for `user_id` (must be an internal user) and returns
  it **once**. Keys are labelled with an `mcp:` prefix.

`res.users.mcp_revoke_apikeys(user_id) -> int`
- Same group gate; revokes all `mcp:`-prefixed keys for the user (rotation/offboarding),
  returns the count removed.

## Security model
- Methods are gated on a dedicated least-privilege group **"MCP API Key Provisioning"**
  (xml_id `mcp_apikey_provisioning.group_mcp_provisioning`), **not** `base.group_system`.
  System admins are **not** implicit members — assign the group explicitly.
- The MCP service account must be a member of that group (and an internal user).
- **Anti-privilege-escalation:** minting is **refused** for the superuser and for any
  target in an elevated group (`base.group_system`, `base.group_erp_manager`). A
  compromised service-account key therefore cannot mint an administrator's credentials.
- Target must be an **internal** user (not portal/share).
- Minted keys are **`rpc`-scoped** and time-limited: TTL defaults to **30 days** and is
  **hard-capped at 90 days** server-side (`MAX_KEY_TTL_DAYS`), regardless of the requested
  value.
- Methods return only freshly generated keys; they never disclose existing keys.

## Install

1. Copy this directory into your Odoo addons path.
2. Update the apps list and install **MCP API Key Provisioning**.
3. Add the MCP service account to the **MCP API Key Provisioning** group.

> Note: this addon calls the low-level `_generate` directly, so it does **not** require
> the `base.enable_programmatic_api_keys` system parameter. The MCP server stores minted
> keys encrypted and re-mints on expiry/revocation.
