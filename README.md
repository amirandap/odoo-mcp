# Odoo MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server that lets
AI assistants like Claude securely interact with an Odoo 18 ERP instance — with
**per-user OAuth 2.1 identity**, **employee self-service** workflows, generic
record CRUD, and optional document signing.

## Why this server?

Several Odoo MCP servers already exist (Odoo's own `mcp_server` addon, MuK MCP,
and community XML-RPC bridges such as `ivnvxd/mcp-server-odoo`). Most of them are
**generic CRUD bridges that act with a single shared admin credential**.

This server is different — it is built around **authenticated end users**:

- **OAuth 2.1 + PKCE** with a per-request bearer token; each request is mapped to
  the calling user's Odoo employee record.
- **Employee self-service tools** — your own profile, manager, team, leave
  balance/requests, public holidays, and HR documents — all automatically scoped
  to the authenticated user, never to arbitrary records.
- **Granular OAuth scopes** per tool (`odoo.read`, `odoo.hr.profile`,
  `odoo.leave.write`, …) instead of all-or-nothing access.
- Generic **CRUD tools** are still available for admin/integration scopes.

If you want a per-user, HR-focused, auth-first server, this is for you. If you
just need an admin-credential CRUD bridge, the community servers above are
simpler.

## Features

- **OAuth 2.1 / PKCE** authentication (Google as the identity provider out of the
  box; pluggable via `OAUTH_PROVIDER=custom`).
- **30 MCP tools**: 7 generic CRUD, 16 employee self-service, 7 optional signing.
- **Two transports**: streamable HTTP (remote MCP) and stdio (local clients).
- **Per-user scoping**: employee identity is derived from the OAuth token, never
  from tool input.
- **Configurable for any Odoo**: custom employee fields, document folders, and
  the optional signing module are all opt-in via environment variables.
- **Production ready**: Docker image, CI, and a reference Cloud Run deployment.

## Quick Start

### Prerequisites

- Python 3.12+
- An Odoo 18 instance and an API key (or username/password)
- A Google OAuth client (for production auth) — or run with `OAUTH_DEV_MODE=true`
  locally

### Installation

```bash
git clone https://github.com/OWNER/odoo-mcp-server.git
cd odoo-mcp-server

python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Configuration

Copy `.env.example` to `.env` and fill in your values. Minimum to start:

```bash
ODOO_URL=https://your-odoo-instance.example.com
ODOO_DB=your-database-name
ODOO_API_KEY=your_api_key_here

# OAuth (production). For local dev you can instead set OAUTH_DEV_MODE=true.
OAUTH_PROVIDER=google
OAUTH_CLIENT_ID=your-google-client-id.apps.googleusercontent.com
OAUTH_CLIENT_SECRET=your_client_secret_here
OAUTH_RESOURCE_IDENTIFIER=https://your-mcp-server.example.com
OAUTH_REDIRECT_URI=https://your-mcp-server.example.com/oauth/callback
```

Required settings are validated at startup — the server **fails fast** with a
clear message if any are missing (no silent defaults).

### Running the Server

```bash
# HTTP server (remote MCP / Claude.ai connector)
python -m odoo_mcp_server.http_server

# stdio (local MCP clients such as Claude Desktop)
python -m odoo_mcp_server.server
```

## Available Tools

### Generic CRUD (scopes: `odoo.read` / `odoo.write`)

| Tool | Description |
|------|-------------|
| `search_records` | Search records with domain filters |
| `get_record` | Get a single record by ID |
| `create_record` | Create a new record |
| `update_record` | Update an existing record |
| `delete_record` | Delete a record |
| `count_records` | Count records matching a domain |
| `list_models` | List available Odoo models |

### Employee self-service (HR / leave / document scopes)

Profile & org: `get_my_profile`, `get_my_manager`, `get_my_team`,
`find_colleague`, `get_direct_reports`, `update_my_contact`.
Leave: `get_my_leave_balance`, `get_my_leave_requests`, `request_leave`,
`cancel_leave_request`, `get_public_holidays`.
Documents: `get_my_documents`, `get_document_categories`,
`upload_identity_document`, `download_document`, `get_document_details`.

All of these are automatically restricted to the authenticated user's own data.

### Document signing — optional (scopes: `odoo.sign.*`)

Disabled by default. Requires the community **OCA `sign_oca`** addon
(`sign.oca.*` models, *not* Odoo Enterprise Sign). Enable with
`SIGN_MODULE_ENABLED=true` to expose: `get_my_pending_signatures`,
`get_my_signature_requests`, `get_signature_request_status`,
`list_sign_templates`, `send_signature_request`, `download_signed_document`,
`cancel_signature_request`.

## Optional Features

These default to stock-Odoo-safe values, so the server works against a vanilla
Odoo with no extra configuration.

| Setting | Purpose |
|---------|---------|
| `ENABLED_TOOL_GROUPS` | Comma-separated tool groups to expose: `crud`, `employee` (default `crud,employee`). Use `crud` for a generic admin bridge or `employee` for self-service only. |
| `EMPLOYEE_CUSTOM_FIELDS` | JSON map of output key → custom `hr.employee` field, e.g. `{"preferred_name":"x_preferred_name","division":"x_division"}`. Surfaced on `get_my_profile`. |
| `DMS_ALLOWED_FOLDERS` / `DMS_RESTRICTED_FOLDERS` | Comma-separated document folder names employees may see vs. must never see. |
| `SIGN_MODULE_ENABLED` | Expose the OCA Sign tools (requires the `sign_oca` addon). |
| `INTERNAL_EMAIL_DOMAIN` | Users whose email matches this domain receive extended write scopes. |
| `YOLO_MODE` | `read` = no-auth read-only (only read tools exposed); `true` = no-auth full access. For trusted/local use only. |

### Deployment modes

The server runs anywhere from a single trusted-network Odoo with no auth, to a
multi-user, multi-environment OAuth deployment. See
**[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** for copy-paste recipes (no-auth/read-only,
single-identity API key, Google OAuth, custom OIDC, stdio) and how to pick tool groups.

## Claude Desktop Integration

```json
{
  "mcpServers": {
    "odoo": {
      "url": "https://your-deployment-url/mcp",
      "transport": "streamable-http"
    }
  }
}
```

See [docs/CLAUDE_INTEGRATION.md](docs/CLAUDE_INTEGRATION.md) for the full
Claude.ai connector setup (Google OAuth redirect URIs, scopes, etc.).

## Testing

```bash
pytest tests/unit          # fast, no external services
pytest tests/integration   # requires a real Odoo (skips without credentials)
pytest --cov=src/ --cov-report=html
```

Unit tests run out of the box — `conftest.py` provides placeholder Odoo settings
so nothing external is needed.

## Deployment

### Docker

```bash
docker build -t odoo-mcp-server -f docker/Dockerfile .
docker run -p 8080:8080 \
  -e ODOO_URL=https://your-odoo-instance.example.com \
  -e ODOO_DB=your-database-name \
  -e ODOO_API_KEY=your_key \
  odoo-mcp-server
```

### Google Cloud Run

A reference Cloud Run deployment is wired into `.github/workflows/ci.yml`. Set
the `GCP_REGION` repository variable and the secrets listed in
[docs/CI_CD.md](docs/CI_CD.md), then merge to `main`.

## Project Structure

```
odoo-mcp-server/
├── src/odoo_mcp_server/
│   ├── server.py          # stdio MCP entry point
│   ├── http_server.py     # HTTP transport + OAuth flow
│   ├── config.py          # settings, scopes, rate limits
│   ├── oauth/             # OAuth 2.1 resource server, token validation
│   ├── odoo/              # Odoo XML-RPC client
│   ├── tools/             # MCP tools (records, employee, sign)
│   └── resources/         # MCP resources (model discovery)
├── tests/                 # unit / integration / e2e
├── docker/                # Docker configuration
├── terraform/             # optional AI/security reviewer infrastructure
└── docs/                  # security, CI/CD, Claude integration
```

## Security

- OAuth 2.1 with PKCE for all authentication flows
- JWT validation against the provider's JWKS
- Employee identity derived from the token, never from user input
- All employee queries constrained to the authenticated user
- Credentials loaded from the environment only — never hardcoded

Report vulnerabilities via [SECURITY.md](SECURITY.md). See
[docs/SECURITY.md](docs/SECURITY.md) for the security model.

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
