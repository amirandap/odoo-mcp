"""Configuration management for Odoo MCP Server."""

import sys

from pydantic_settings import BaseSettings

# =============================================================================
# OAuth Scope Definitions (Feedback 4.1 - Granular Scopes)
# =============================================================================

OAUTH_SCOPES = {
    # Standard OpenID scopes
    "openid": "OpenID Connect authentication",
    "profile": "User profile information",
    "email": "User email address",

    # Generic Odoo scopes (read/write any model)
    "odoo.read": "Read any Odoo data",
    "odoo.write": "Write any Odoo data",

    # HR/Employee-specific scopes (granular access)
    "odoo.hr.profile": "Read own employee profile",
    "odoo.hr.profile.write": "Update own employee contact information",
    "odoo.hr.team": "Read team/department members",
    "odoo.hr.directory": "Search employee directory",

    # Leave management scopes
    "odoo.leave.read": "Read own leave balance and requests",
    "odoo.leave.write": "Create/cancel leave requests",
    "odoo.leave.approve": "Approve team leave requests (managers)",

    # Document management scopes
    "odoo.documents.read": "Read own HR documents",
    "odoo.documents.write": "Upload identity documents",

    # Sign module scopes
    "odoo.sign.read": "Read signature requests and templates",
    "odoo.sign.write": "Send and cancel signature requests",
}

# Write-bearing scopes. Used to derive a read-only scope set (see read_only_scopes).
WRITE_SCOPES = {
    "odoo.write",
    "odoo.hr.profile.write",
    "odoo.leave.write",
    "odoo.leave.approve",
    "odoo.documents.write",
    "odoo.sign.write",
}


def read_only_scopes() -> list[str]:
    """All defined scopes minus the write-bearing ones (for read-only deployments)."""
    return [scope for scope in OAUTH_SCOPES if scope not in WRITE_SCOPES]


# Scope requirements for each tool
TOOL_SCOPE_REQUIREMENTS = {
    # Profile tools (Employee Self-Service)
    "get_my_profile": ["odoo.hr.profile", "odoo.read"],
    "get_my_manager": ["odoo.hr.profile", "odoo.read"],
    "get_my_team": ["odoo.hr.team", "odoo.read"],
    "find_colleague": ["odoo.hr.directory", "odoo.read"],
    "get_direct_reports": ["odoo.hr.team", "odoo.read"],
    "update_my_contact": ["odoo.hr.profile.write", "odoo.write"],

    # Leave tools (Employee Self-Service)
    "get_my_leave_balance": ["odoo.leave.read", "odoo.read"],
    "get_my_leave_requests": ["odoo.leave.read", "odoo.read"],
    "request_leave": ["odoo.leave.write", "odoo.write"],
    "cancel_leave_request": ["odoo.leave.write", "odoo.write"],
    "get_public_holidays": ["odoo.leave.read", "odoo.read"],

    # Document tools (Employee Self-Service)
    "get_my_documents": ["odoo.documents.read", "odoo.read"],
    "get_document_categories": ["odoo.documents.read", "odoo.read"],
    "upload_identity_document": ["odoo.documents.write", "odoo.write"],
    "download_document": ["odoo.documents.read", "odoo.read"],
    "get_document_details": ["odoo.documents.read", "odoo.read"],

    # Sign tools (Employee Self-Service)
    "get_my_pending_signatures": ["odoo.sign.read", "odoo.read"],
    "get_my_signature_requests": ["odoo.sign.read", "odoo.read"],
    "get_signature_request_status": ["odoo.sign.read", "odoo.read"],
    "list_sign_templates": ["odoo.sign.read", "odoo.read"],
    "send_signature_request": ["odoo.sign.write", "odoo.write"],
    "download_signed_document": ["odoo.sign.read", "odoo.read"],
    "cancel_signature_request": ["odoo.sign.write", "odoo.write"],

    # Generic CRUD tools (Admin only - requires odoo.write for most operations)
    "search_records": ["odoo.read"],
    "get_record": ["odoo.read"],
    "create_record": ["odoo.write"],
    "update_record": ["odoo.write"],
    "delete_record": ["odoo.write"],
    "count_records": ["odoo.read"],
    "list_models": ["odoo.read"],
}

# =============================================================================
# Rate Limiting Configuration
# =============================================================================

RATE_LIMITS = {
    "read_operations_per_minute": 30,
    "write_operations_per_hour": 10,
    "document_uploads_per_day": 5,
}

# Tools classified by operation type for rate limiting
WRITE_TOOLS = [
    "request_leave",
    "cancel_leave_request",
    "upload_identity_document",
    "update_my_contact",
    "create_record",
    "update_record",
    "delete_record",
    "send_signature_request",
    "cancel_signature_request",
]

UPLOAD_TOOLS = [
    "upload_identity_document",
]


def check_scope_access(required_scopes: list[str], granted_scopes: list[str]) -> bool:
    """
    Check if any of the required scopes are in the granted scopes.

    Args:
        required_scopes: List of scopes that would grant access (any one is sufficient)
        granted_scopes: List of scopes the user has

    Returns:
        True if user has at least one required scope
    """
    return any(scope in granted_scopes for scope in required_scopes)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Odoo connection (REQUIRED - no defaults, must be set via environment)
    odoo_url: str  # e.g. https://your-odoo-instance.example.com
    odoo_db: str  # e.g. your-database-name
    odoo_api_key: str | None = None
    odoo_username: str | None = None
    odoo_password: str | None = None

    # OAuth 2.1 Configuration (Google OAuth)
    # Provider: "google" or "custom"
    oauth_provider: str = "google"

    # Google OAuth settings (REQUIRED for production)
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None

    # Google OAuth endpoints (defaults for Google - public well-known URLs)
    oauth_authorization_server: str = "https://accounts.google.com"
    oauth_authorization_endpoint: str = "https://accounts.google.com/o/oauth2/v2/auth"
    oauth_token_endpoint: str = "https://oauth2.googleapis.com/token"
    oauth_jwks_uri: str = "https://www.googleapis.com/oauth2/v3/certs"
    oauth_issuer: str = "https://accounts.google.com"

    # Resource Server settings (REQUIRED - your deployment URL)
    oauth_resource_identifier: str | None = None
    oauth_redirect_uri: str | None = None

    # OAuth scopes requested
    oauth_scopes: str = "openid email profile"

    # Token Storage (Feedback 4.1)
    # Options: "memory" (default), "redis", "encrypted_file"
    token_storage_backend: str = "memory"
    token_encryption_key: str | None = None  # For encrypted storage

    # HTTP Server (0.0.0.0 needed for Docker container networking)
    http_host: str = "0.0.0.0"  # nosec B104 - required for container deployment
    http_port: int = 8080

    # Internal domain for granting extended scopes (e.g. "example.com")
    internal_email_domain: str | None = None

    # Explicit allowlist (comma-separated emails) of users who get full CRUD access
    # (odoo.read + odoo.write on ANY model). Everyone else who authenticates only
    # gets the employee self-service scopes (safe by design — auto-scoped to their
    # own hr.employee record). This is a deliberate manual allowlist, NOT automatic
    # by email domain or OAuth group membership.
    crud_admin_emails: str = ""

    # --- Optional per-instance feature configuration (defaults = stock Odoo) ---
    # Extra hr.employee fields surfaced on the self profile.
    # JSON mapping of output key -> Odoo field name, e.g.
    #   EMPLOYEE_CUSTOM_FIELDS={"preferred_name": "x_preferred_name", "division": "x_division"}
    # Empty by default so the server runs against a stock Odoo with no custom fields.
    employee_custom_fields: dict[str, str] = {}

    # Document (DMS) folder names, comma-separated. These map to the folder/category
    # names in your Odoo Documents/DMS setup.
    dms_allowed_folders: str = "Contracts,Identity"
    dms_restricted_folders: str = "Background Checks,Offboarding Documents"

    # Tool groups to expose, comma-separated. Available: "crud", "employee".
    # ("sign" is controlled by sign_module_enabled, but may also be listed here.)
    # Examples: "crud" for a generic admin bridge, "employee" for self-service only.
    enabled_tool_groups: str = "crud,employee"

    # OCA Sign module (sign_oca). Disabled by default because it requires the
    # community `sign_oca` addon (sign.oca.* models). Enable to expose the sign tools.
    sign_module_enabled: bool = False

    # API key authentication (for CLI clients like Claude Code that can't do browser OAuth)
    mcp_api_key: str | None = None  # Secret key loaded from GCP Secret Manager
    mcp_api_key_email: str | None = None  # Email identity for API key user

    # Development
    debug: bool = False
    oauth_dev_mode: bool = False  # Skip OAuth validation in dev
    yolo_mode: str | None = None  # "read" for read-only, "true" for full access

    model_config = {
        "env_prefix": "",
        "case_sensitive": False,
    }

    @property
    def effective_issuer(self) -> str:
        """Return effective OAuth issuer."""
        return self.oauth_issuer or self.oauth_authorization_server

    @property
    def is_google_oauth(self) -> bool:
        """Check if using Google OAuth."""
        return self.oauth_provider.lower() == "google"

    @property
    def effective_tool_groups(self) -> set[str]:
        """Tool groups to expose: configured groups plus 'sign' when the module is enabled."""
        groups = {g.strip().lower() for g in self.enabled_tool_groups.split(",") if g.strip()}
        if self.sign_module_enabled:
            groups.add("sign")
        return groups

    @property
    def crud_admin_emails_set(self) -> set[str]:
        """Emails with full CRUD (odoo.read/odoo.write) access, parsed from the comma-separated setting."""
        return {e.strip().lower() for e in self.crud_admin_emails.split(",") if e.strip()}

    @property
    def dms_allowed_folders_list(self) -> list[str]:
        """DMS folders employees may view/upload, parsed from the comma-separated setting."""
        return [f.strip() for f in self.dms_allowed_folders.split(",") if f.strip()]

    @property
    def dms_restricted_folders_list(self) -> list[str]:
        """DMS folders employees must never see, parsed from the comma-separated setting."""
        return [f.strip() for f in self.dms_restricted_folders.split(",") if f.strip()]


def validate_settings(settings: Settings) -> None:
    """Validate that all required settings are present. Fail fast at startup."""
    missing = []

    if not settings.odoo_url:
        missing.append("ODOO_URL")
    if not settings.odoo_db:
        missing.append("ODOO_DB")
    if not settings.odoo_api_key and not settings.odoo_password:
        missing.append("ODOO_API_KEY (or ODOO_PASSWORD)")

    if not settings.oauth_dev_mode and not settings.yolo_mode:
        if not settings.oauth_client_id:
            missing.append("OAUTH_CLIENT_ID")
        if not settings.oauth_client_secret:
            missing.append("OAUTH_CLIENT_SECRET")
        if not settings.oauth_redirect_uri:
            missing.append("OAUTH_REDIRECT_URI")

    if missing:
        print(
            f"FATAL: Missing required environment variables: {', '.join(missing)}",
            file=sys.stderr,
        )
        print(
            "Copy .env.example to .env and fill in your values.",
            file=sys.stderr,
        )
        sys.exit(1)
