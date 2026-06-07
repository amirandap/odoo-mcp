"""OdooVaultGoogleProvider — Google OAuth + per-user Odoo API key injection.

Per request:
1. ``GoogleProvider`` validates the FastMCP token and resolves the Google identity
   (``claims["email"]``).
2. We look up that user's Odoo API key in the vault, **auto-minting** one via the
   companion addon on first use.
3. We return an ``AccessToken`` whose ``token`` IS the Odoo API key, tagged
   ``auth_method=odoo_api_key`` with the user's email, so downstream tools build a
   per-user Odoo client and act as the real user.

If the user is authenticated but a key cannot be minted yet (addon not installed, or no
internal Odoo user), the original (key-less) token is returned so ``enrollment_status``
still works; Odoo tools then fail with a clear message.

Adapted from plane-mcp-bridge's auth.py (which uses user-pasted PATs; here we auto-mint).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import anyio
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.providers.google import GoogleProvider

from .clients import build_admin_client
from .provisioning import ProvisioningError, mint_user_key
from .vault import Vault

logger = logging.getLogger("odoo_mcp_bridge.auth")

_KEY_CACHE_TTL_SECONDS = 300


class OdooVaultGoogleProvider(GoogleProvider):
    """Google OAuth provider that substitutes the user's Odoo API key as the access token."""

    def __init__(
        self,
        *,
        vault: Vault,
        config,
        is_email_allowed: Callable[[str], bool] | None = None,
        **google_kwargs,
    ) -> None:
        super().__init__(**google_kwargs)
        self._vault = vault
        self._config = config
        self._is_email_allowed = is_email_allowed
        self._admin_client = build_admin_client(config)
        self._key_cache: dict[str, tuple[str | None, float]] = {}
        # Proactively re-mint once a stored key has used ~80% of its TTL, so the bridge
        # never serves a key that Odoo is about to expire (floor of 60s for tiny TTLs).
        self._rotate_after = max(60.0, config.key_ttl_days * 86400 * 0.8)

    async def _lookup_or_mint(self, email: str) -> str | None:
        """Return the user's Odoo API key, minting and storing one on first use."""
        cached = self._key_cache.get(email)
        now = time.time()
        if cached and cached[1] > now:
            return cached[0]

        # Vault backends may be synchronous; keep them off the event loop. Passing the
        # rotation age makes the vault return None for a near-expiry key -> we re-mint.
        key = await anyio.to_thread.run_sync(self._vault.get_key, email, self._rotate_after)
        if not key:
            try:
                key = await mint_user_key(self._admin_client, email, self._config.key_ttl_days)
                await anyio.to_thread.run_sync(self._vault.put_key, email, key)
            except ProvisioningError as exc:
                logger.warning("Could not provision Odoo key for a user: %s", exc)
                key = None

        self._key_cache[email] = (key, now + _KEY_CACHE_TTL_SECONDS)
        return key

    def invalidate(self, email: str) -> None:
        """Drop a cached/stored key (e.g. after an Unauthorized) so the next call re-mints."""
        self._key_cache.pop(email, None)
        try:
            self._vault.delete_key(email)
        except Exception:  # noqa: BLE001 - best effort
            pass

    async def verify_token(self, token: str) -> AccessToken | None:
        verified = await super().verify_token(token)
        if verified is None:
            return None

        email = ((verified.claims or {}).get("email") or "").strip().lower()
        if not email:
            logger.warning("Authenticated token has no email claim; rejecting")
            return None

        # Defense in depth: enforce the allowlist at the token boundary too.
        if self._is_email_allowed is not None and not self._is_email_allowed(email):
            logger.warning("Rejecting token for non-allowlisted email")
            return None

        key = await self._lookup_or_mint(email)
        if not key:
            # Authenticated but no Odoo key yet — keep authenticated so enrollment_status works.
            return verified

        return AccessToken(
            token=key,
            client_id=email,
            scopes=verified.scopes or ["read", "write"],
            expires_at=verified.expires_at,
            claims={"auth_method": "odoo_api_key", "email": email},
        )
