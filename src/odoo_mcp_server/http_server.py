"""
HTTP MCP Server with OAuth 2.1 Support

Provides HTTP transport for MCP protocol with OAuth authentication.
"""

import asyncio
import hashlib
import hmac
import html
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.responses import StreamingResponse

from .config import OAUTH_SCOPES, TOOL_SCOPE_REQUIREMENTS, Settings, check_scope_access, read_only_scopes
from .oauth.resource_server import (
    OAuthResourceServer,
    extract_user_context,
)
from .oauth.user_mapping import EmployeeNotFoundError, get_employee_for_user
from .odoo.client import OdooClient
from .resources import read_resource, register_resources
from .tools import execute_tool, register_tools, tool_group_for
from .tools.employee import EMPLOYEE_TOOLS, execute_employee_tool
from .tools.employee import configure as configure_employee_tools
from .tools.sign import SIGN_TOOLS, execute_sign_tool

logger = logging.getLogger(__name__)


def _rate_limit_key(request: Request) -> str:
    """Rate limit key: user email if authenticated, otherwise IP."""
    user = getattr(request.state, "user", None)
    if user and user.get("email"):
        return user["email"]
    return get_remote_address(request)


# Global state
settings = Settings()  # type: ignore[call-arg]  # pydantic-settings loads from env vars
odoo_client: OdooClient | None = None

# Apply per-instance tool configuration (custom employee fields, DMS folder names).
configure_employee_tools(
    custom_fields=settings.employee_custom_fields,
    dms_allowed_folders=settings.dms_allowed_folders_list,
    dms_restricted_folders=settings.dms_restricted_folders_list,
)

# =============================================================================
# Pending OAuth Authorization Sessions
# =============================================================================
# In-memory store for OAuth proxy flow. Maps state -> client session data.
# When a client calls /authorize, we store their redirect_uri keyed by state,
# redirect to Google with our own callback URL, then forward back to the client.

AUTH_SESSION_TTL_SECONDS = 600  # 10 minutes

_pending_auth_sessions: dict[str, dict] = {}


def _store_auth_session(state: str, client_redirect_uri: str) -> None:
    """Store a pending auth session keyed by state."""
    _cleanup_expired_sessions()
    _pending_auth_sessions[state] = {
        "client_redirect_uri": client_redirect_uri,
        "created_at": time.time(),
    }


def _get_auth_session(state: str) -> dict | None:
    """Retrieve and remove a pending auth session (one-time use)."""
    session = _pending_auth_sessions.pop(state, None)
    if session and (time.time() - session["created_at"]) < AUTH_SESSION_TTL_SECONDS:
        return session
    return None


def _cleanup_expired_sessions() -> None:
    """Remove expired sessions to prevent memory leaks."""
    now = time.time()
    expired = [s for s, data in _pending_auth_sessions.items()
               if (now - data["created_at"]) >= AUTH_SESSION_TTL_SECONDS]
    for s in expired:
        del _pending_auth_sessions[s]


# Allowed redirect URI patterns for OAuth clients
ALLOWED_REDIRECT_PATTERNS = [
    # Claude.ai and Anthropic Console
    ("https", "claude.ai"),
    ("https", "console.anthropic.com"),
    # MCP clients running locally (Claude Code CLI, mcp-remote, etc.)
    ("http", "localhost"),
    ("http", "127.0.0.1"),
]


def _validate_redirect_uri(uri: str) -> bool:
    """Validate redirect_uri against allowlist to prevent open redirects."""
    try:
        parsed = urlparse(uri)
    except Exception:
        return False

    # Must have scheme and netloc
    if not parsed.scheme or not parsed.netloc:
        return False

    # Check hostname (strip port for comparison)
    hostname = parsed.hostname or ""

    for allowed_scheme, allowed_host in ALLOWED_REDIRECT_PATTERNS:
        if parsed.scheme == allowed_scheme and hostname == allowed_host:
            return True

    # Also allow the server's own resource identifier domain
    if settings and settings.oauth_resource_identifier:
        server_parsed = urlparse(settings.oauth_resource_identifier)
        if parsed.scheme == "https" and hostname == server_parsed.hostname:
            return True

    return False


def _get_oauth_audience() -> str:
    """
    Get the appropriate OAuth audience based on provider.

    For Google OAuth: audience is the client_id (Google ID tokens have aud=client_id)
    For custom OAuth: audience is the resource identifier
    """
    if settings.is_google_oauth and settings.oauth_client_id:
        return settings.oauth_client_id
    return settings.oauth_resource_identifier or ""


def _get_advertised_scopes() -> list[str]:
    """
    Get scopes to advertise in OAuth metadata.

    For Google OAuth: only advertise standard OpenID scopes (Google doesn't understand custom scopes)
    For custom OAuth: advertise all Odoo scopes
    """
    if settings.is_google_oauth:
        # Only advertise scopes that Google understands
        return ["openid", "email", "profile"]
    return list(OAUTH_SCOPES.keys())


# Initialized in lifespan(); lazy fallback via _get_resource_server().
resource_server: OAuthResourceServer | None = None


def _get_resource_server() -> OAuthResourceServer:
    """Return the resource server, creating a default instance if lifespan hasn't run yet."""
    global resource_server
    if resource_server is None:
        resource_server = OAuthResourceServer(
            resource=settings.oauth_resource_identifier or "",
            authorization_servers=[settings.oauth_resource_identifier or settings.oauth_authorization_server],
            audience=_get_oauth_audience(),
            scopes_supported=_get_advertised_scopes(),
            issuer=settings.effective_issuer,
        )
    return resource_server


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global odoo_client, resource_server

    # Initialize Odoo client
    logger.info(f"Initializing Odoo client: {settings.odoo_url} (DB: {settings.odoo_db}, User: {settings.odoo_username or 'admin'})")
    odoo_client = OdooClient(
        url=settings.odoo_url,
        db=settings.odoo_db,
        api_key=settings.odoo_api_key,
        username=settings.odoo_username,
        password=settings.odoo_password,
    )

    # Initialize OAuth resource server
    # authorization_servers = our server (for MCP client discovery)
    # issuer = Google (for token validation, since Google issues the tokens)
    resource_server = OAuthResourceServer(
        resource=settings.oauth_resource_identifier or "",
        authorization_servers=[settings.oauth_resource_identifier or settings.oauth_authorization_server],
        audience=_get_oauth_audience(),
        scopes_supported=_get_advertised_scopes(),
        issuer=settings.effective_issuer,
    )

    logger.info(f"OAuth provider: {settings.oauth_provider}")
    logger.info(f"OAuth issuer: {settings.oauth_issuer}")
    logger.info(f"OAuth audience: {_get_oauth_audience()}")

    logger.info(f"HTTP MCP Server started on {settings.http_host}:{settings.http_port}")

    yield

    # Cleanup
    if odoo_client:
        await odoo_client.close()


# Create FastAPI app
app = FastAPI(
    title="Odoo MCP Server",
    description="MCP server for Odoo with OAuth 2.1 authentication",
    version="0.1.0",
    lifespan=lifespan,
)

# =============================================================================
# Rate Limiting
# =============================================================================

limiter = Limiter(key_func=_rate_limit_key, enabled=not settings.oauth_dev_mode)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """Return 429 with Retry-After header when rate limit is exceeded."""
    return JSONResponse(
        status_code=429,
        content={"error": "rate_limit_exceeded", "error_description": "Too many requests. Please slow down."},
        headers={"Retry-After": str(exc.detail)},
    )


# =============================================================================
# Security Configuration
# =============================================================================

# Allowed CORS origins (restrict from wildcard for security)
ALLOWED_ORIGINS = [
    "https://claude.ai",
    "https://console.anthropic.com",
    "https://app.slack.com",
    # Add localhost for development only when DEBUG is enabled
]

if settings.debug:
    ALLOWED_ORIGINS.extend([
        "http://localhost:3000",
        "http://localhost:8080",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8080",
    ])

# Add CORS middleware with restricted origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)


# Security headers middleware
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Add security headers to all responses."""
    response = await call_next(request)

    # Prevent MIME type sniffing
    response.headers["X-Content-Type-Options"] = "nosniff"

    # Prevent clickjacking
    response.headers["X-Frame-Options"] = "DENY"

    # Enable XSS filter (legacy but still useful)
    response.headers["X-XSS-Protection"] = "1; mode=block"

    # Strict Transport Security (enforce HTTPS for 1 year)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    # Referrer policy - don't leak referrer to third parties
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    # Content Security Policy - restrict resource loading
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "frame-ancestors 'none'"
    )

    return response


# Add OAuth middleware (after security headers, after CORS so preflight requests work)
@app.middleware("http")
async def oauth_middleware(request: Request, call_next):
    """OAuth authentication middleware."""
    # Skip auth for certain paths
    skip_paths = [
        "/health",
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-authorization-server",
        "/callback",
        "/",
        "/authorize",
        "/token",
        "/register",
    ]

    # Normalize path (handle trailing slashes)
    path = request.url.path
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    if path in skip_paths:
        return await call_next(request)

    # Extract token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={"error": "unauthorized", "error_description": "Missing Bearer token"},
            headers={"WWW-Authenticate": 'Bearer realm="odoo-mcp"'},
        )

    token = auth_header[7:]

    # Log token metadata only (hash for correlation, never content)
    token_hash = hashlib.sha256(token.encode()).hexdigest()[:16]
    logger.debug(f"Received token: hash={token_hash}")

    # In dev/test mode, skip validation (only when explicitly enabled via environment)
    is_test_mode = settings.oauth_dev_mode or settings.yolo_mode
    if is_test_mode:
        import os
        dev_email = os.getenv("TEST_USER_EMAIL") or "dev@example.com"
        # YOLO_MODE=read grants a read-only scope set; dev mode and YOLO_MODE=true grant all.
        if settings.yolo_mode == "read":
            scopes = read_only_scopes()
            logger.info("OAuth dev mode (read-only): using configured test email")
        else:
            scopes = list(OAUTH_SCOPES.keys())
            logger.info("OAuth dev mode: using configured test email")
        request.state.user = {
            "sub": "dev-user",
            "email": dev_email,
            "employee_id": None,
            "scopes": scopes,
            "claims": {},
        }
        return await call_next(request)

    # API key authentication (for CLI clients like Claude Code)
    if settings.mcp_api_key and hmac.compare_digest(token, settings.mcp_api_key):
        email = settings.mcp_api_key_email or "api-key-user@localhost"
        logger.info("API key auth: authenticated as %s", email)
        request.state.user = {
            "sub": "api-key-user",
            "email": email,
            "employee_id": None,
            "scopes": list(OAUTH_SCOPES.keys()),
            "claims": {"email": email, "email_verified": True},
        }
        return await call_next(request)

    # Validate token
    logger.debug("Validating token")
    rs = _get_resource_server()
    if rs:
        try:
            claims = await rs.validate_token_async(token)
            request.state.user = extract_user_context(
                claims,
                internal_email_domain=settings.internal_email_domain,
                crud_admin_emails=settings.crud_admin_emails_set,
            )
            return await call_next(request)
        except Exception as e:
            logger.warning(f"Token validation failed: {type(e).__name__}")
            logger.debug(f"Token validation detail: {type(e).__name__}, hash={token_hash}")
            return JSONResponse(
                status_code=401,
                content={"error": "invalid_token", "error_description": "Token validation failed"},
                headers={"WWW-Authenticate": 'Bearer realm="odoo-mcp", error="invalid_token"'},
            )

    return await call_next(request)


# =============================================================================
# Health & Metadata Endpoints
# =============================================================================


CODE_VERSION = "2026-03-10-v11-oauth-proxy-flow"


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "odoo-mcp-server", "code_version": CODE_VERSION}


@app.get("/")
async def root():
    """Root endpoint for service discovery."""
    return {"status": "ok", "service": "odoo-mcp-server"}


@app.get("/.well-known/oauth-protected-resource")
async def oauth_protected_resource_metadata():
    """RFC 9728 Protected Resource Metadata endpoint."""
    rs = _get_resource_server()
    if not rs:
        raise HTTPException(status_code=503, detail="OAuth not configured")

    return rs.metadata.to_dict()


@app.get("/authorize")
@limiter.limit("10/minute")
async def oauth_authorize(
    request: Request,
    response_type: str = "code",
    client_id: str | None = None,
    redirect_uri: str | None = None,
    scope: str | None = None,
    state: str | None = None,
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
):
    """
    OAuth authorization proxy - redirects to Google using server's own callback.

    Stores the client's redirect_uri and forwards to Google with the server's
    registered callback URL. After Google authenticates, /callback will redirect
    back to the client's original redirect_uri.
    """
    # Generate state if not provided (needed as session key)
    if not state:
        state = str(uuid.uuid4())

    # Store client's redirect_uri for later forwarding from /callback
    client_redirect = redirect_uri or "https://claude.ai/api/mcp/auth_callback"

    if not _validate_redirect_uri(client_redirect):
        logger.warning("Rejected invalid redirect_uri: %s", client_redirect[:100])
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_request", "error_description": "Invalid redirect_uri"},
        )

    _store_auth_session(state=state, client_redirect_uri=client_redirect)

    # Use server's own callback URL (registered with Google)
    server_callback = settings.oauth_redirect_uri
    if not server_callback:
        server_callback = f"{settings.oauth_resource_identifier}/callback"

    params = {
        "response_type": response_type,
        "client_id": settings.oauth_client_id,
        "redirect_uri": server_callback,
        "scope": settings.oauth_scopes,
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = code_challenge_method or "S256"

    # Filter None values
    params = {k: v for k, v in params.items() if v is not None}

    logger.info("OAuth authorize: state=%s client_redirect=%s", state[:8], client_redirect[:50])

    return RedirectResponse(
        url=f"{settings.oauth_authorization_endpoint}?{urlencode(params)}",
        status_code=302,
    )


@app.post("/token")
@limiter.limit("20/minute")
async def oauth_token(request: Request):
    """
    OAuth token endpoint - proxies to Google's token endpoint.

    Supports authorization_code and refresh_token grant types.
    Always uses the server's own redirect_uri and credentials with Google.
    """
    form = await request.form()
    grant_type = str(form.get("grant_type", ""))

    # Log all form fields (redact secrets)
    form_keys = list(form.keys())
    logger.info("Token request: grant_type=%s form_keys=%s", grant_type, form_keys)

    client_id = settings.oauth_client_id or ""
    client_secret = settings.oauth_client_secret or ""

    if grant_type == "authorization_code":
        code = str(form.get("code", ""))
        code_verifier = str(form.get("code_verifier", "")) if form.get("code_verifier") else None

        if not code:
            logger.warning("Token request missing authorization code")
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_request", "error_description": "Missing authorization code"},
            )

        # Use server's callback URL (must match what was sent to Google in /authorize)
        server_callback = settings.oauth_redirect_uri or f"{settings.oauth_resource_identifier}/callback"

        logger.info(
            "Token exchange: code=%s... redirect_uri=%s has_verifier=%s",
            code[:10], server_callback, code_verifier is not None,
        )

        token_data: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": server_callback,
            "client_id": client_id,
            "client_secret": client_secret,
        }

        if code_verifier:
            token_data["code_verifier"] = code_verifier

    elif grant_type == "refresh_token":
        refresh_token = str(form.get("refresh_token", ""))

        if not refresh_token:
            logger.warning("Token request missing refresh_token")
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_request", "error_description": "Missing refresh_token"},
            )

        token_data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }

    else:
        logger.warning("Unsupported grant_type: %s", grant_type)
        return JSONResponse(
            status_code=400,
            content={
                "error": "unsupported_grant_type",
                "error_description": "Supported grant types: authorization_code, refresh_token",
            },
        )

    # Exchange with the configured OAuth provider's token endpoint (Google by default,
    # or a custom OIDC provider when OAUTH_PROVIDER=custom).
    async with httpx.AsyncClient() as client:
        response = await client.post(
            settings.oauth_token_endpoint,
            data=token_data,
        )

    result = response.json()
    if response.status_code != 200:
        logger.error(
            "Token exchange FAILED: status=%s grant_type=%s error=%s",
            response.status_code, grant_type, result.get("error_description", result.get("error", "unknown")),
        )
    else:
        logger.info("Token exchange OK: grant_type=%s has_refresh=%s", grant_type, "refresh_token" in result)

    return JSONResponse(
        status_code=response.status_code,
        content=result,
    )


@app.get("/.well-known/oauth-authorization-server")
async def oauth_authorization_server_metadata():
    """
    RFC 8414 OAuth Authorization Server Metadata.

    This helps MCP clients discover our OAuth endpoints.
    """
    base_url = settings.oauth_resource_identifier
    return {
        "issuer": base_url,
        "authorization_endpoint": f"{base_url}/authorize",
        "token_endpoint": f"{base_url}/token",
        "registration_endpoint": f"{base_url}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "none"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": ["openid", "email", "profile"],
    }


@app.post("/register")
@limiter.limit("5/minute")
async def oauth_register(request: Request):
    """
    RFC 7591 Dynamic Client Registration endpoint.

    Required by MCP Authorization spec for clients like mcp-remote.
    Since this server proxies OAuth to Google with its own credentials,
    the registered client_id is not used for upstream auth -- it only
    satisfies the MCP client handshake.

    Claude.ai does not use this endpoint (it has its own OAuth flow).
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    client_id = str(uuid.uuid4())
    client_secret = str(uuid.uuid4())

    # Return standard RFC 7591 response.
    # Include client_secret because some MCP clients (e.g. Claude Code) require it
    # for the token exchange. Our server ignores the client's secret -- it always
    # uses its own Google credentials when proxying to Google's token endpoint.
    response = {
        "client_id": client_id,
        "client_secret": client_secret,
        "client_name": body.get("client_name", "mcp-client"),
        "redirect_uris": body.get("redirect_uris", []),
        "grant_types": body.get("grant_types", ["authorization_code", "refresh_token"]),
        "response_types": body.get("response_types", ["code"]),
        "token_endpoint_auth_method": "client_secret_post",
    }

    logger.info("Dynamic client registration: client_id=%s", client_id[:8])
    return JSONResponse(status_code=201, content=response)


@app.get("/callback")
async def oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    """
    OAuth callback - receives authorization code from Google and forwards to client.

    Google redirects here after user consent. We look up the client's original
    redirect_uri from the pending session and redirect the user there.
    """
    if error:
        # Forward error to client if we have a pending session
        if state:
            session = _get_auth_session(state)
            if session:
                separator = "&" if "?" in session["client_redirect_uri"] else "?"
                params = urlencode({"error": error, "state": state})
                error_url = f"{session['client_redirect_uri']}{separator}{params}"
                return RedirectResponse(url=error_url, status_code=302)

        safe_error = html.escape(error)
        return HTMLResponse(
            content=f"<html><body><h1>OAuth Error</h1><p>{safe_error}</p></body></html>",
            status_code=400,
        )

    if not code:
        return HTMLResponse(
            content="<html><body><h1>OAuth Callback</h1><p>Missing authorization code.</p></body></html>",
            status_code=400,
        )

    if not state:
        return HTMLResponse(
            content="<html><body><h1>OAuth Callback</h1><p>Missing state parameter.</p></body></html>",
            status_code=400,
        )

    # Look up the original client redirect URI
    session = _get_auth_session(state)

    if session:
        # Redirect to client's original redirect_uri with code and state
        client_redirect = session["client_redirect_uri"]
        separator = "&" if "?" in client_redirect else "?"
        # URL-encode params properly (Google's code contains / and other chars)
        params = urlencode({"code": code, "state": state})
        redirect_url = f"{client_redirect}{separator}{params}"

        logger.info("OAuth callback: redirecting to client (state=%s)", state[:8])
        return RedirectResponse(url=redirect_url, status_code=302)

    # No pending session - fall back to postMessage for backward compatibility
    logger.warning("OAuth callback: no pending session for state=%s, using postMessage fallback", state[:8] if state else "none")
    callback_json = json.dumps({
        "type": "oauth_callback",
        "code": code,
        "state": state or "",
    }).replace("</", r"<\/")

    return HTMLResponse(
        content=f"""
        <html>
        <body>
        <h1>Authorization Successful</h1>
        <p>You can close this window and return to the application.</p>
        <script>
            if (window.opener) {{
                window.opener.postMessage({callback_json}, 'https://claude.ai');
            }}
        </script>
        </body>
        </html>
        """,
        status_code=200,
    )


# =============================================================================
# MCP Protocol Endpoint
# =============================================================================


class MCPRequest(BaseModel):
    """MCP JSON-RPC request."""

    jsonrpc: str = "2.0"
    method: str
    params: dict[str, Any] | None = None
    id: int | str | None = None


def _make_json_serializable(obj: Any) -> Any:
    """Convert Pydantic types and other non-serializable objects to plain Python types."""
    if isinstance(obj, dict):
        return {k: _make_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_make_json_serializable(v) for v in obj]
    if hasattr(obj, "__str__") and not isinstance(obj, (str, int, float, bool, type(None))):
        # Handles Pydantic AnyUrl, etc.
        return str(obj)
    return obj


class MCPResponse(JSONResponse):
    """MCP JSON-RPC response that omits null fields (JSON-RPC 2.0 compliant)."""

    def __init__(
        self,
        id: int | str | None = None,
        result: Any = None,
        error: dict[str, Any] | None = None,
        **kwargs: Any,
    ):
        content: dict[str, Any] = {"jsonrpc": "2.0"}
        if id is not None:
            content["id"] = id
        if error is not None:
            content["error"] = error
        elif result is not None:
            content["result"] = _make_json_serializable(result)
        super().__init__(content=content, **kwargs)


@app.get("/mcp")
async def mcp_sse_endpoint(request: Request):
    """
    MCP SSE endpoint for server-initiated messages (Streamable HTTP 2025-03-26).

    Returns 405 if server does not support server-initiated messages via GET.
    Per spec: server MUST either return text/event-stream or 405 Method Not Allowed.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    async def event_generator():
        """Generate SSE keep-alive stream."""
        # Keep connection alive with heartbeats (no server-initiated messages for now)
        while True:
            await asyncio.sleep(30)
            yield ": heartbeat\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.delete("/mcp")
async def mcp_delete_session(request: Request):
    """
    MCP session termination (Streamable HTTP 2025-03-26).

    Clients send DELETE to terminate a session.
    """
    logger.info("MCP session terminated by client")
    return Response(status_code=200)


@app.post("/mcp")
@limiter.limit("60/minute")
async def mcp_endpoint(request: Request, mcp_request: MCPRequest):
    """
    MCP JSON-RPC endpoint (Streamable HTTP transport, spec 2025-03-26).

    Handles MCP protocol methods:
    - initialize: Protocol initialization with version negotiation
    - notifications/*: Return 202 Accepted (no body)
    - tools/list: List available tools
    - tools/call: Execute a tool
    - resources/list: List available resources
    - resources/read: Read a resource
    - ping: Health check
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    method = mcp_request.method
    params = mcp_request.params or {}

    try:
        # Handle notifications (no id, return 202 Accepted per MCP 2025-03-26 spec)
        if method.startswith("notifications/"):
            logger.info("MCP notification: %s", method)
            return Response(status_code=202)

        if method == "initialize":
            # MCP protocol initialization (2025-03-26)
            session_id = str(uuid.uuid4())
            result = {
                "protocolVersion": "2025-03-26",
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                },
                "serverInfo": {
                    "name": "odoo-mcp-server",
                    "version": "0.1.0",
                },
            }
            logger.info("MCP initialize: session=%s user=%s", session_id[:8], user.get("email", "?"))
            response = JSONResponse(
                content={"jsonrpc": "2.0", "id": mcp_request.id, "result": result},
            )
            response.headers["Mcp-Session-Id"] = session_id
            return response
        elif method == "tools/list":
            result = await handle_tools_list(user)
        elif method == "tools/call":
            result = await handle_tools_call(params, user)
        elif method == "resources/list":
            result = await handle_resources_list(user)
        elif method == "resources/read":
            result = await handle_resources_read(params, user)
        elif method == "ping":
            result = {}
        else:
            return MCPResponse(
                id=mcp_request.id,
                error={"code": -32601, "message": f"Method not found: {method}"},
            )

        return MCPResponse(id=mcp_request.id, result=result)

    except HTTPException as e:
        return MCPResponse(
            id=mcp_request.id,
            error={"code": -32000, "message": e.detail},
        )
    except Exception as e:
        logger.exception(f"Error handling MCP request: {type(e).__name__}")
        return MCPResponse(
            id=mcp_request.id,
            error={"code": -32603, "message": "Internal server error"},
        )


async def handle_tools_list(user: dict) -> dict:
    """Handle tools/list MCP method."""
    all_tools = register_tools(settings.effective_tool_groups)
    user_scopes = user.get("scopes", [])

    # Filter tools based on user's scopes
    accessible_tools = []
    for tool in all_tools:
        required_scopes = TOOL_SCOPE_REQUIREMENTS.get(tool.name, ["odoo.read"])
        if check_scope_access(required_scopes, user_scopes):
            accessible_tools.append({
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.inputSchema,
            })

    logger.info(
        "tools/list: total=%d accessible=%d scope_count=%d",
        len(all_tools),
        len(accessible_tools),
        len(user_scopes),
    )
    return {"tools": accessible_tools}


async def handle_tools_call(params: dict, user: dict) -> dict:
    """Handle tools/call MCP method."""
    tool_name = params.get("name")
    arguments = params.get("arguments", {})

    if not tool_name:
        raise HTTPException(status_code=400, detail="Missing tool name")

    # Check scope access
    user_scopes = user.get("scopes", [])
    required_scopes = TOOL_SCOPE_REQUIREMENTS.get(tool_name, ["odoo.read"])

    if not check_scope_access(required_scopes, user_scopes):
        logger.warning(f"Insufficient scope for tool {tool_name}. Required: {required_scopes}, Granted: {user_scopes}")
        raise HTTPException(
            status_code=403,
            detail=f"Insufficient scope for tool: {tool_name}",
        )

    if not odoo_client:
        raise HTTPException(status_code=503, detail="Odoo client not initialized")

    # Reject tools whose group is not enabled on this deployment.
    group = tool_group_for(tool_name)
    if group is not None and group not in settings.effective_tool_groups:
        detail = (
            "Sign module is not enabled on this server"
            if group == "sign"
            else f"Tool group '{group}' is not enabled on this server"
        )
        raise HTTPException(status_code=404, detail=detail)

    # Check if this is an employee self-service tool or sign tool
    employee_tool_names = [t.name for t in EMPLOYEE_TOOLS]
    sign_tool_names = [t.name for t in SIGN_TOOLS]
    is_employee_tool = tool_name in employee_tool_names
    is_sign_tool = tool_name in sign_tool_names

    try:
        if is_employee_tool or is_sign_tool:
            # Resolve employee_id from OAuth user context
            employee_id = user.get("employee_id")

            if not employee_id:
                # Map OAuth claims to employee
                try:
                    claims = user.get("claims", {})
                    # Add email from user context if not in claims
                    if "email" not in claims:
                        claims["email"] = user.get("email")

                    employee_info = await get_employee_for_user(claims, odoo_client)
                    employee_id = employee_info["id"]
                    logger.info("Resolved employee for authenticated user")
                except EmployeeNotFoundError as e:
                    logger.warning(f"Employee not found: {type(e).__name__}")
                    raise HTTPException(
                        status_code=403,
                        detail="No Odoo employee record is linked to your account",
                    )
                except Exception as e:
                    logger.exception(f"Error resolving employee: {type(e).__name__}")
                    raise HTTPException(
                        status_code=500,
                        detail="Error resolving employee account. Please contact support.",
                    )

            if is_sign_tool:
                # Execute sign tool with employee context
                result = await execute_sign_tool(tool_name, arguments, odoo_client, employee_id)
            else:
                # Execute employee tool with employee context
                result = await execute_employee_tool(tool_name, arguments, odoo_client, employee_id)
        else:
            # Execute generic tool (CRUD - only for admin users with odoo.write scope)
            result = await execute_tool(tool_name, arguments, odoo_client)

        return {
            "content": [{"type": "text", "text": r.text} for r in result],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error executing tool {tool_name}: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="Internal error executing tool")


async def handle_resources_list(user: dict) -> dict:
    """Handle resources/list MCP method."""
    all_resources = register_resources()

    return {
        "resources": [
            {
                "uri": r.uri,
                "name": r.name,
                "description": r.description,
                "mimeType": r.mimeType,
            }
            for r in all_resources
        ]
    }


async def handle_resources_read(params: dict, user: dict) -> dict:
    """Handle resources/read MCP method."""
    uri = params.get("uri")
    if not uri:
        raise HTTPException(status_code=400, detail="Missing resource URI")

    if not odoo_client:
        raise HTTPException(status_code=503, detail="Odoo client not initialized")

    content = await read_resource(uri, odoo_client)

    return {
        "contents": [{"uri": uri, "text": content}],
    }


# =============================================================================
# Main Entry Point
# =============================================================================


def main():
    """Run HTTP server."""
    import uvicorn

    uvicorn.run(
        "odoo_mcp_server.http_server:app",
        host=settings.http_host,
        port=settings.http_port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    main()
