"""
OAuth 2.1 Resource Server Implementation

Provides FastAPI middleware for OAuth token validation and user context extraction.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .metadata import ProtectedResourceMetadata
from .token_validator import TokenValidationError, TokenValidator

logger = logging.getLogger(__name__)


def extract_user_context(
    claims: dict[str, Any],
    internal_email_domain: str | None = None,
    crud_admin_emails: set[str] | None = None,
) -> dict[str, Any]:
    """
    Extract user context from validated token claims.

    Args:
        claims: Decoded JWT claims
        internal_email_domain: Kept for backward compatibility with existing
            callers. No longer used to grant odoo.write (see security note
            below) - retained only so it can still be passed without raising.
        crud_admin_emails: Explicit, manually maintained allowlist (lowercased
            emails) of users who get full CRUD access (odoo.read + odoo.write
            on ANY model). Set via CRUD_ADMIN_EMAILS env var
            (Settings.crud_admin_emails_set).

    Returns:
        User context dictionary with:
        - email: User's email address
        - employee_id: Odoo employee ID (if present)
        - scopes: List of granted scopes
        - sub: Subject identifier

    Note:
        Many OIDC providers' ID tokens don't include a 'scope' claim (Google's
        included). For any provider, we grant default employee self-service
        scopes based on email verification when no explicit odoo.* scopes are
        present. Those defaults are safe to auto-grant to any authenticated
        user because every employee tool self-limits to the caller's own
        hr.employee record (see oauth/user_mapping.py).

        "odoo.read" and "odoo.write" are NOT part of those defaults: they
        grant full, unfiltered CRUD access to every Odoo model via the
        generic tools (search_records, get_record, create_record,
        update_record, delete_record, count_records), which have no
        "only your own records" filtering. Previously "odoo.read" was granted
        to any verified user and "odoo.write" to anyone whose email matched
        internal_email_domain - in a deployment where any employee can
        authenticate via the OIDC provider (e.g. Pocket ID with passkeys),
        that meant any employee could read/write the entire Odoo database
        through the shared service-account connection. Both scopes are now
        gated behind the explicit crud_admin_emails allowlist instead.
    """
    # Extract scopes (space-separated string to list)
    scope_string = claims.get("scope", "")
    scopes = scope_string.split() if scope_string else []

    # For any OAuth provider (Google or a custom OIDC provider, e.g. a private
    # self-hosted IdP), if Odoo scopes are missing, grant default scopes.
    # This handles both ID tokens (no scope claim) and Access tokens (standard
    # scopes only). By the time claims reach this function they have already
    # been validated (signature/issuer/audience) by TokenValidator against
    # whichever issuer this deployment is configured for, so a verified email
    # from that issuer is itself the trust signal - regardless of which
    # provider it is.
    has_odoo_scopes = any(s.startswith("odoo.") for s in scopes)

    if not has_odoo_scopes:
        email = claims.get("email", "")
        email_verified = claims.get("email_verified", False)

        if email_verified and email:
            # Grant default employee self-service scopes for any verified user.
            # Safe to auto-grant: every employee tool self-limits to the
            # caller's own hr.employee record. Deliberately does NOT include
            # odoo.read/odoo.write - those grant full, unfiltered CRUD access
            # to every Odoo model and are gated behind crud_admin_emails below.
            default_scopes = [
                "odoo.hr.profile",
                "odoo.hr.team",
                "odoo.hr.directory",
                "odoo.leave.read",
                "odoo.leave.write",
                "odoo.documents.read",
                "odoo.sign.read",
            ]

            # Add to existing scopes (preserving openid, etc.)
            for scope in default_scopes:
                if scope not in scopes:
                    scopes.append(scope)

            logger.info("OAuth: granted default employee self-service scopes for verified user")

            # Grant full CRUD access (odoo.read + odoo.write) only to emails on
            # the explicit, manually maintained allowlist. This is deliberately
            # NOT automatic by email domain or OAuth group membership - see the
            # security note in this function's docstring.
            if crud_admin_emails and email.lower() in crud_admin_emails:
                for scope in ("odoo.read", "odoo.write"):
                    if scope not in scopes:
                        scopes.append(scope)
                logger.info("OAuth: granted full CRUD scopes to allowlisted admin user")

    return {
        "sub": claims.get("sub"),
        "email": claims.get("email", claims.get("sub")),
        "employee_id": claims.get("odoo_employee_id"),
        "scopes": scopes,
        "claims": claims,
    }


@dataclass
class OAuthResourceServer:
    """
    OAuth 2.1 Resource Server configuration.

    Manages token validation and protected resource metadata.

    Note: ``authorization_servers`` is advertised in RFC 9728 metadata so MCP
    clients know where to start the OAuth flow.  ``issuer`` is the actual token
    issuer used for JWT validation.  When acting as an OAuth proxy (e.g. our
    server proxies to Google), these differ: ``authorization_servers`` points to
    *our* server while ``issuer`` remains ``https://accounts.google.com``.
    """

    resource: str
    authorization_servers: list[str]
    audience: str
    scopes_supported: list[str] = field(default_factory=list)
    issuer: str | None = None

    # Internal components
    _validator: TokenValidator | None = field(default=None, repr=False)
    _metadata: ProtectedResourceMetadata | None = field(default=None, repr=False)

    def __post_init__(self):
        """Initialize internal components."""
        # Use explicit issuer if provided, otherwise fall back to first
        # authorization server (backwards-compatible default).
        token_issuer = self.issuer or (self.authorization_servers[0] if self.authorization_servers else None)
        if token_issuer:
            self._validator = TokenValidator(
                issuer=token_issuer,
                audience=self.audience,
            )

        self._metadata = ProtectedResourceMetadata(
            resource=self.resource,
            authorization_servers=self.authorization_servers,
            scopes_supported=self.scopes_supported,
        )

    @property
    def metadata(self) -> ProtectedResourceMetadata:
        """Get protected resource metadata."""
        if not self._metadata:
            raise RuntimeError("Metadata not initialized")
        return self._metadata

    @property
    def validator(self) -> TokenValidator:
        """Get token validator."""
        if not self._validator:
            raise RuntimeError("No authorization server configured")
        return self._validator

    def validate_token(self, token: str) -> dict[str, Any]:
        """Validate access token and return claims."""
        return self.validator.validate(token)

    async def validate_token_async(self, token: str) -> dict[str, Any]:
        """Validate access token asynchronously."""
        return await self.validator.validate_async(token)


class OAuthMiddleware(BaseHTTPMiddleware):
    """
    FastAPI/Starlette middleware for OAuth token validation.

    Validates Bearer tokens in Authorization header and adds
    user context to request.state.
    """

    def __init__(
        self,
        app,
        resource_server: OAuthResourceServer | None = None,
        exclude_paths: list[str] | None = None,
        dev_mode: bool = False,
    ):
        """
        Initialize OAuth middleware.

        Args:
            app: FastAPI/Starlette application
            resource_server: OAuth resource server configuration
            exclude_paths: Paths to exclude from auth (e.g., /health)
            dev_mode: If True, skip validation (for development only)
        """
        super().__init__(app)
        self.resource_server = resource_server
        self.exclude_paths = exclude_paths or [
            "/health",
            "/.well-known/oauth-protected-resource",
            "/callback",
        ]
        self.dev_mode = dev_mode

    def _extract_token(self, request: Request) -> str | None:
        """Extract Bearer token from Authorization header."""
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:]
        return None

    def _should_skip_auth(self, request: Request) -> bool:
        """Check if path should skip authentication."""
        path = request.url.path
        return any(
            path == excluded or path.startswith(f"{excluded}/")
            for excluded in self.exclude_paths
        )

    def _unauthorized_response(self, error: str = "Unauthorized") -> Response:
        """Return 401 Unauthorized response with WWW-Authenticate header."""
        return JSONResponse(
            status_code=401,
            content={"error": "unauthorized", "error_description": error},
            headers={
                "WWW-Authenticate": 'Bearer realm="odoo-mcp", error="invalid_token"'
            },
        )

    def _forbidden_response(self, error: str = "Forbidden") -> Response:
        """Return 403 Forbidden response."""
        return JSONResponse(
            status_code=403,
            content={"error": "insufficient_scope", "error_description": error},
        )

    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
        """Process request through OAuth validation."""

        # Skip auth for excluded paths
        if self._should_skip_auth(request):
            return await call_next(request)

        # Extract token
        token = self._extract_token(request)
        if not token:
            return self._unauthorized_response("Missing Bearer token")

        # Dev mode: skip validation
        if self.dev_mode:
            request.state.user = {
                "sub": "dev-user",
                "email": "dev@example.com",
                "scopes": ["openid", "odoo.read", "odoo.write"],
                "claims": {},
            }
            return await call_next(request)

        # Validate token
        if not self.resource_server:
            return self._unauthorized_response("OAuth not configured")

        try:
            claims = await self.resource_server.validate_token_async(token)
            request.state.user = extract_user_context(claims)
            return await call_next(request)
        except TokenValidationError as e:
            logger.warning(f"Token validation failed: {type(e).__name__}")
            return self._unauthorized_response("Token validation failed")
        except Exception as e:
            logger.error(f"Unexpected error during token validation: {type(e).__name__}")
            return self._unauthorized_response("Token validation failed")


def require_scopes(*required_scopes: str):
    """
    Dependency for requiring specific OAuth scopes.

    Usage:
        @app.get("/api/profile")
        async def get_profile(user: dict = Depends(require_scopes("odoo.hr.profile"))):
            ...
    """
    from fastapi import HTTPException, Request

    async def dependency(request: Request) -> dict:
        user = getattr(request.state, "user", None)
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")

        user_scopes = user.get("scopes", [])
        has_scope = any(scope in user_scopes for scope in required_scopes)

        if not has_scope:
            raise HTTPException(
                status_code=403,
                detail=f"Required scope: {' or '.join(required_scopes)}",
            )

        return user

    return dependency
