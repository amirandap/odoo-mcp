{
    "name": "MCP API Key Provisioning",
    "version": "18.0.1.1.0",
    "summary": "Admin-only minting of per-user API keys for the Odoo MCP server (per-user identity mode).",
    "description": """
Companion addon for the Odoo MCP server's per-user identity mode.

The MCP server (running as a trusted admin/service account) needs to mint an
`rpc`-scoped API key on behalf of an authenticated end user, so that subsequent
Odoo operations run as that user (native record rules + correct create_uid),
instead of as the shared service account.

Stock Odoo does not expose a way to mint an API key for *another* user over
XML-RPC (`res.users.apikeys.generate` is not an RPC method and `_generate` is
private). This addon adds a single admin-guarded model method,
`res.users.mcp_mint_apikey`, that mints a key for a target user via the
documented low-level `_generate` on that user's environment.

Security: the methods raise AccessError unless the *caller* is a member of the
dedicated least-privilege group "MCP API Key Provisioning"
(xml_id: mcp_apikey_provisioning.group_mcp_provisioning) -- NOT full system admin.
They never return another user's existing key -- only a freshly generated one, once.
""",
    "category": "Technical",
    "author": "AI Cognitive Leap",
    "license": "LGPL-3",
    "depends": ["base"],
    "data": ["security/mcp_security.xml"],
    "installable": True,
    "application": False,
}
