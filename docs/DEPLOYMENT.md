# Deployment & Auth Modes

This server is designed to be **versatile**: it works for a single company running
one Odoo instance with no auth infrastructure, all the way up to a multi-user,
multi-environment OAuth deployment. Pick the mode that matches your setup.

All settings are environment variables (see [`.env.example`](../.env.example)).
Required settings are validated at startup — the server fails fast if any are
missing.

## Choosing tool groups

Regardless of auth mode, you choose which tool groups are exposed via
`ENABLED_TOOL_GROUPS` (comma-separated):

| Group | Tools | Typical use |
|-------|-------|-------------|
| `crud` | `search_records`, `get_record`, `create_record`, `update_record`, `delete_record`, `count_records`, `list_models` | Generic admin/integration bridge |
| `employee` | profile, manager, team, directory, leave, documents (16 tools) | Employee self-service |
| `sign` | OCA Sign tools (7) | Document signing (also needs `SIGN_MODULE_ENABLED=true` + the `sign_oca` addon) |

- Default: `ENABLED_TOOL_GROUPS=crud,employee`.
- CRUD-only admin bridge: `ENABLED_TOOL_GROUPS=crud`.
- Employee self-service only: `ENABLED_TOOL_GROUPS=employee`.
- Sign tools appear when `SIGN_MODULE_ENABLED=true` (or `sign` is listed in the groups).

Disabled groups are hidden from `tools/list` and calling them returns 404.

---

## Mode 1 — Single Odoo, no auth (trusted network / local)

Simplest possible setup. No OAuth, no identity provider. Use only on a trusted
network or behind your own gateway.

```bash
ODOO_URL=https://odoo.internal.example.com
ODOO_DB=mycompany
ODOO_API_KEY=...
# Bypass OAuth entirely:
YOLO_MODE=read        # read-only: only read tools are exposed
# YOLO_MODE=true      # full read/write access
# OAUTH_DEV_MODE=true # equivalent to YOLO_MODE=true (all scopes)
TEST_USER_EMAIL=svc@example.com   # email used to map to an Odoo employee for employee tools
```

- `YOLO_MODE=read` grants a **read-only** scope set, so write tools
  (`create_record`, `request_leave`, `update_my_contact`, …) are not exposed.
- `YOLO_MODE=true` / `OAUTH_DEV_MODE=true` grant all scopes.
- Rate limiting is disabled in these modes.

## Mode 2 — Single Odoo, single shared identity (API key)

One static bearer token, mapped to one identity. Good for a single integration or
a CLI client (e.g. Claude Code) without a browser OAuth flow.

```bash
ODOO_URL=https://odoo.internal.example.com
ODOO_DB=mycompany
ODOO_API_KEY=...
MCP_API_KEY=<generate a strong random string>
MCP_API_KEY_EMAIL=svc@example.com   # mapped to this Odoo employee for employee tools
```

Clients send `Authorization: Bearer <MCP_API_KEY>`. The request is granted all
scopes and acts as `MCP_API_KEY_EMAIL`.

## Mode 3 — Multi-user, Google OAuth (default)

Per-user identity via Google. Each user authenticates; their email maps to their
Odoo employee record, and employee tools are scoped to their own data.

```bash
ODOO_URL=https://odoo.example.com
ODOO_DB=mycompany
ODOO_API_KEY=...
OAUTH_PROVIDER=google
OAUTH_CLIENT_ID=...apps.googleusercontent.com
OAUTH_CLIENT_SECRET=...
OAUTH_RESOURCE_IDENTIFIER=https://your-mcp-server.example.com
OAUTH_REDIRECT_URI=https://your-mcp-server.example.com/oauth/callback
INTERNAL_EMAIL_DOMAIN=example.com   # users on this domain get extended write scopes
```

## Mode 4 — Multi-user, custom OIDC provider

Use any OIDC provider (Keycloak, Auth0, Okta, …) instead of Google.

```bash
OAUTH_PROVIDER=custom
OAUTH_AUTHORIZATION_SERVER=https://idp.example.com
OAUTH_AUTHORIZATION_ENDPOINT=https://idp.example.com/authorize
OAUTH_TOKEN_ENDPOINT=https://idp.example.com/oauth/token
OAUTH_JWKS_URI=https://idp.example.com/.well-known/jwks.json
OAUTH_ISSUER=https://idp.example.com/
OAUTH_RESOURCE_IDENTIFIER=https://your-mcp-server.example.com
OAUTH_REDIRECT_URI=https://your-mcp-server.example.com/oauth/callback
```

With a custom provider, scopes come from the token claims (the server advertises the
full Odoo scope set in its OAuth metadata).

## Mode 5 — stdio, local (service account)

Run the stdio transport for a local MCP client (e.g. Claude Desktop) using a service
account. No OAuth, no HTTP. Best with `ENABLED_TOOL_GROUPS=crud` (employee tools need
a per-user identity, which stdio does not establish).

```bash
ODOO_URL=https://odoo.example.com
ODOO_DB=mycompany
ODOO_API_KEY=...
ENABLED_TOOL_GROUPS=crud
```

```bash
python -m odoo_mcp_server.server
```

---

## CI behaviour for single vs. two environments

See [CI_CD.md](CI_CD.md). In short:

- **Single instance (no staging):** leave `STAGING_ODOO_URL` and `REQUIRE_STAGING`
  unset — the staging integration/e2e/deploy jobs skip and CI stays green.
- **Two environments:** set the `STAGING_ODOO_URL` secret (jobs run). Optionally set
  the `REQUIRE_STAGING=true` repo variable so a *missing* staging URL fails CI loudly.
