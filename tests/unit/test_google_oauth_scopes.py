"""
Tests for Google OAuth Scope Handling in Resource Server

Verifies that default Odoo scopes are granted to Google OAuth users
even when their token contains standard OIDC scopes.
"""
import pytest

from odoo_mcp_server.oauth.resource_server import extract_user_context

pytestmark = [pytest.mark.unit, pytest.mark.oauth]

def test_google_token_with_standard_scopes_grants_default_odoo_scopes():
    """
    Test that a Google token with 'openid email profile' scopes still gets
    default Odoo scopes (odoo.read, etc.) if email is verified.
    """
    # Simulate a Google Access Token claims
    claims = {
        "iss": "https://accounts.google.com",
        "sub": "1234567890",
        "email": "test@example.com",
        "email_verified": True,
        "scope": "openid email profile",  # Standard OIDC scopes
        "aud": "my-client-id"
    }

    context = extract_user_context(claims)
    scopes = context["scopes"]

    # Verify standard scopes are preserved
    assert "openid" in scopes
    assert "email" in scopes
    assert "profile" in scopes

    # Verify Odoo default scopes are ADDED
    assert "odoo.read" in scopes
    assert "odoo.hr.profile" in scopes
    assert "odoo.leave.read" in scopes

def test_google_token_with_custom_odoo_scopes_does_not_add_defaults():
    """
    Test that if a Google token ALREADY has odoo scopes (e.g. from a custom flow),
    we respect them and do not just add defaults blindly (though defaults might be subset).
    Actually logic says: if NOT has_odoo_scopes, then add defaults.
    """
    claims = {
        "iss": "https://accounts.google.com",
        "sub": "1234567890",
        "email": "test@example.com",
        "email_verified": True,
        "scope": "openid odoo.custom.scope",  # Has an odoo scope
        "aud": "my-client-id"
    }

    context = extract_user_context(claims)
    scopes = context["scopes"]

    assert "odoo.custom.scope" in scopes
    # Defaults should NOT be added because has_odoo_scopes is True
    assert "odoo.read" not in scopes

def test_non_google_token_also_gets_defaults_after_generalization():
    """
    extract_user_context() no longer special-cases Google: any verified,
    scope-less token (any `iss`) gets the default Odoo scopes.

    This intentionally supersedes the old Google-only behavior. In a custom
    OAUTH_PROVIDER=custom deployment (e.g. a private self-hosted IdP like
    Pocket ID), the token has already been validated by TokenValidator
    against the deployment's configured issuer/jwks/audience before it
    reaches this function - successfully authenticating with that private
    IdP is itself the trust signal, exactly as it is today for Google.
    """
    claims = {
        "iss": "https://other-issuer.com",
        "sub": "user123",
        "email": "test@example.com",
        "email_verified": True,
        "scope": "openid email",
    }

    context = extract_user_context(claims)
    scopes = context["scopes"]

    assert "openid" in scopes
    assert "odoo.read" in scopes
    assert "odoo.hr.profile" in scopes


def test_custom_provider_token_with_verified_email_grants_default_scopes():
    """
    A token from a private, self-hosted OIDC provider (e.g. Pocket ID) with
    a verified email and no explicit odoo.* scopes should still be granted
    the default employee self-service scopes, just like Google tokens are.
    """
    claims = {
        "iss": "https://id.example-internal.com",
        "sub": "pocketid-user-1",
        "email": "employee@example-internal.com",
        "email_verified": True,
        "scope": "openid profile email",
    }

    context = extract_user_context(claims)
    scopes = context["scopes"]

    assert "odoo.read" in scopes
    assert "odoo.hr.profile" in scopes
    assert "odoo.leave.read" in scopes


def test_custom_provider_token_without_email_verified_gets_no_defaults():
    """
    Regression: an unverified email from a custom provider must not get
    default scopes either - this check is provider-agnostic, not removed.
    """
    claims = {
        "iss": "https://id.example-internal.com",
        "sub": "pocketid-user-2",
        "email": "employee@example-internal.com",
        "email_verified": False,
        "scope": "openid profile email",
    }

    context = extract_user_context(claims)
    scopes = context["scopes"]

    assert "openid" in scopes
    assert "odoo.read" not in scopes

def test_google_internal_user_gets_write_access():
    """
    Test that internal users (@example.com) get write access.
    """
    claims = {
        "iss": "https://accounts.google.com",
        "sub": "1234567890",
        "email": "dev@example.com",
        "email_verified": True,
        "scope": "openid email profile",
    }

    context = extract_user_context(claims, internal_email_domain="example.com")
    scopes = context["scopes"]

    assert "odoo.write" in scopes
    assert "odoo.documents.write" in scopes
    assert "odoo.sign.write" in scopes
    assert "odoo.read" in scopes


def test_google_external_user_no_write_scopes():
    """
    Test that external users do not get write scopes even when internal_email_domain is set.
    """
    claims = {
        "iss": "https://accounts.google.com",
        "sub": "1234567890",
        "email": "external@other.com",
        "email_verified": True,
        "scope": "openid email profile",
    }

    context = extract_user_context(claims, internal_email_domain="example.com")
    scopes = context["scopes"]

    assert "odoo.write" not in scopes
    assert "odoo.documents.write" not in scopes
    assert "odoo.sign.write" not in scopes
    # But they should still get read scopes
    assert "odoo.read" in scopes
    assert "odoo.sign.read" in scopes

def test_google_token_without_email_verified_gets_no_defaults():
    """
    Test that unverified email gets no extra scopes.
    """
    claims = {
        "iss": "https://accounts.google.com",
        "sub": "1234567890",
        "email": "test@example.com",
        "email_verified": False,
        "scope": "openid email profile",
    }

    context = extract_user_context(claims)
    scopes = context["scopes"]

    assert "openid" in scopes
    assert "odoo.read" not in scopes
