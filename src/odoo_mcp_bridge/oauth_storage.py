"""Persistent storage backend for the OAuth proxy's client/token state.

FastMCP's ``OAuthProxy`` keeps registered DCR clients, issued refresh-token metadata, and
in-flight OAuth transactions in a pluggable ``client_storage`` (an ``AsyncKeyValue``). It
**defaults to local disk**, which on Cloud Run is ephemeral and per-instance — so with more
than one instance (or any restart/redeploy) clients and refresh tokens are lost and users
are forced to re-authenticate.

Set ``OAUTH_CLIENT_STORAGE`` to a **shared** backend so OAuth state survives:
  - ``firestore`` (recommended on Cloud Run; serverless, shared, no VPC connector) — uses GCP_PROJECT.
  - ``redis``     — uses OAUTH_CLIENT_STORAGE_REDIS_URL (needs Memorystore + VPC connector).
  - ``memory``    — process-local (tests only).
  - unset/``disk``/``default`` — FastMCP's default local disk (single-instance / local dev only).
"""

from __future__ import annotations

import logging

logger = logging.getLogger("odoo_mcp_bridge.oauth_storage")


def build_client_storage(config):
    """Return an AsyncKeyValue for the OAuth proxy, or None to use FastMCP's default (disk).

    Lazy-imports the backend driver so the dependency is only required when selected.
    """
    backend = (getattr(config, "oauth_client_storage", "") or "").strip().lower()

    if backend in ("", "disk", "default", "none"):
        logger.warning(
            "OAUTH_CLIENT_STORAGE not set: OAuth proxy state uses local disk, which is "
            "EPHEMERAL and PER-INSTANCE on Cloud Run. Set OAUTH_CLIENT_STORAGE=firestore "
            "for a multi-instance / persistent deployment (else clients + refresh tokens are "
            "lost on restart/scale)."
        )
        return None

    if backend == "memory":
        from key_value.aio.stores.memory import MemoryStore

        return MemoryStore()

    if backend == "firestore":
        if not getattr(config, "gcp_project", None):
            raise RuntimeError("OAUTH_CLIENT_STORAGE=firestore requires GCP_PROJECT to be set.")
        from key_value.aio.stores.firestore import FirestoreStore

        # Log a constant message only — do not interpolate config-derived values (the config
        # object also carries secrets, which static analysis taints as sensitive).
        logger.info("OAuth proxy state: Firestore backend selected (shared, persistent).")
        return FirestoreStore(
            project=config.gcp_project,
            default_collection=config.oauth_client_storage_collection,
        )

    if backend == "redis":
        if not getattr(config, "oauth_client_storage_redis_url", None):
            raise RuntimeError("OAUTH_CLIENT_STORAGE=redis requires OAUTH_CLIENT_STORAGE_REDIS_URL.")
        from key_value.aio.stores.redis import RedisStore

        logger.info("OAuth proxy state: Redis backend selected (shared, persistent).")
        return RedisStore(
            url=config.oauth_client_storage_redis_url,
            default_collection=config.oauth_client_storage_collection,
        )

    raise RuntimeError(f"Unknown OAUTH_CLIENT_STORAGE backend: {backend!r} (use firestore|redis|memory|disk).")
