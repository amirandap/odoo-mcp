import logging
from datetime import datetime, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

# API keys minted for the MCP server are tagged with this prefix in their name,
# so they can be listed/revoked/rotated as a group.
MCP_KEY_NAME_PREFIX = "mcp:"

# Default lifetime for a minted key when the caller does not specify one.
DEFAULT_KEY_TTL_DAYS = 30

# Least-privilege group gating the provisioning methods (NOT base.group_system).
PROVISIONING_GROUP = "mcp_apikey_provisioning.group_mcp_provisioning"


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model
    def mcp_mint_apikey(self, user_id, name=None, ttl_days=None):
        """Mint a fresh `rpc`-scoped API key for ``user_id`` and return it once.

        Intended to be called over RPC by the MCP server authenticated as a
        system administrator. The key lets the MCP server act as the target
        user for subsequent calls (native ACLs + correct create_uid).

        :param int user_id: target res.users id to mint the key for.
        :param str name: optional label; always stored with the ``mcp:`` prefix.
        :param int ttl_days: optional key lifetime in days (default 30).
        :returns: the freshly generated API key string (shown only once).
        :raises AccessError: if the caller is not a system administrator.
        :raises UserError: if the target user is missing or not an internal user.
        """
        # --- authorization: only members of the dedicated provisioning group ---
        if not self.env.user.has_group(PROVISIONING_GROUP):
            raise AccessError(_("Only members of the MCP API Key Provisioning group may mint API keys for other users."))

        target = self.env["res.users"].browse(int(user_id)).exists()
        if not target:
            raise UserError(_("Target user %s does not exist.") % user_id)
        if target.share:
            # Portal/public users cannot use rpc-scoped keys meaningfully.
            raise UserError(_("Target user %s is not an internal user.") % user_id)

        label = name or "per-user key"
        if not label.startswith(MCP_KEY_NAME_PREFIX):
            label = f"{MCP_KEY_NAME_PREFIX}{label}"

        days = int(ttl_days) if ttl_days else DEFAULT_KEY_TTL_DAYS
        expiration = fields.Datetime.to_string(datetime.utcnow() + timedelta(days=days))

        # Generate as the target user via the documented low-level helper. Running
        # in the target user's environment makes the key belong to them; sudo()
        # ensures the create on res.users.apikeys is permitted from this context.
        # The `_generate` signature gained a required `expiration_date` arg in recent
        # Odoo; fall back to the older 2-arg form for compatibility.
        apikeys = self.env["res.users.apikeys"].with_user(target).sudo()
        try:
            api_key = apikeys._generate("rpc", label, expiration)
        except TypeError:
            api_key = apikeys._generate("rpc", label)

        _logger.info("Minted MCP rpc API key for user_id=%s (label=%s, ttl_days=%s)", target.id, label, days)
        return api_key

    @api.model
    def mcp_revoke_apikeys(self, user_id):
        """Revoke all MCP-minted keys for ``user_id``. Group-gated. Returns count removed."""
        if not self.env.user.has_group(PROVISIONING_GROUP):
            raise AccessError(_("Only members of the MCP API Key Provisioning group may revoke API keys for other users."))

        keys = self.env["res.users.apikeys"].sudo().search(
            [("user_id", "=", int(user_id)), ("name", "=like", f"{MCP_KEY_NAME_PREFIX}%")]
        )
        count = len(keys)
        keys.unlink()
        _logger.info("Revoked %s MCP API key(s) for user_id=%s", count, user_id)
        return count
