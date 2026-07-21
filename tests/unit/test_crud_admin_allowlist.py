"""
Tests for the CRUD admin allowlist security fix.

Background: any authenticated user with a verified email used to receive
"odoo.read" automatically, and "odoo.write" automatically if their email
matched INTERNAL_EMAIL_DOMAIN. Both scopes grant full, unfiltered access to
the generic CRUD tools (search_records, get_record, create_record,
update_record, delete_record, count_records) across ALL Odoo models via the
shared service-account connection - these tools have no "only your own
records" filtering, unlike the employee self-service tools.

This meant any employee who could log in via the OIDC provider (e.g. Pocket
ID, where any employee with a passkey can authenticate) automatically had
read access to every record in the company's Odoo instance, and write access
too if their email happened to match the internal domain. Confirmed in
production: an unrestricted count_records against res.partner returned all
2376 records.

Fix: "odoo.read" is no longer part of the automatic default scopes, and
"odoo.write" is no longer granted by email domain. Both are now gated behind
an explicit, manually maintained allowlist (CRUD_ADMIN_EMAILS). Everyone else
who authenticates still gets the employee self-service scopes automatically
(odoo.hr.profile, odoo.leave.read, etc.) - those are safe by design because
every employee tool self-limits to the caller's own hr.employee record via
email matching (see oauth/user_mapping.py).
"""
import pytest

from odoo_mcp_server.config import TOOL_SCOPE_REQUIREMENTS, Settings, check_scope_access
from odoo_mcp_server.oauth.resource_server import extract_user_context

pytestmark = [pytest.mark.unit, pytest.mark.oauth]


class TestCrudAdminEmailsSetting:
    """Tests for Settings.crud_admin_emails_set parsing."""

    def _settings(self, **overrides) -> Settings:
        base = {
            "odoo_url": "https://example.odoo.test",
            "odoo_db": "test",
            "odoo_api_key": "x",
        }
        base.update(overrides)
        return Settings(**base)

    def test_empty_by_default(self):
        settings = self._settings()
        assert settings.crud_admin_emails_set == set()

    def test_parses_comma_separated_emails(self):
        settings = self._settings(crud_admin_emails="amp@softgrouprd.com,other@example.com")
        assert settings.crud_admin_emails_set == {"amp@softgrouprd.com", "other@example.com"}

    def test_normalizes_case_and_whitespace(self):
        settings = self._settings(crud_admin_emails=" AMP@SoftGroupRD.com , Other@Example.com ")
        assert settings.crud_admin_emails_set == {"amp@softgrouprd.com", "other@example.com"}

    def test_ignores_empty_entries(self):
        settings = self._settings(crud_admin_emails="amp@softgrouprd.com,,")
        assert settings.crud_admin_emails_set == {"amp@softgrouprd.com"}


class TestExtractUserContextCrudAllowlist:
    """Tests for extract_user_context()'s new crud_admin_emails gating."""

    def _claims(self, email: str, email_verified: bool = True) -> dict:
        return {
            "iss": "https://id.example-internal.com",
            "sub": "user-1",
            "email": email,
            "email_verified": email_verified,
            "scope": "openid email profile",
        }

    def test_non_admin_verified_user_does_not_get_odoo_read_or_write(self):
        """
        A regular employee (verified email, not on the allowlist) must NOT
        receive odoo.read or odoo.write under any condition - those scopes
        grant full unfiltered CRUD access to every Odoo model.
        """
        claims = self._claims("employee@softgrouprd.com")

        context = extract_user_context(claims, crud_admin_emails={"amp@softgrouprd.com"})
        scopes = context["scopes"]

        assert "odoo.read" not in scopes
        assert "odoo.write" not in scopes

    def test_non_admin_user_still_gets_employee_self_service_scopes(self):
        """Removing odoo.read from the defaults must not break employee self-service."""
        claims = self._claims("employee@softgrouprd.com")

        context = extract_user_context(claims, crud_admin_emails={"amp@softgrouprd.com"})
        scopes = context["scopes"]

        assert "odoo.hr.profile" in scopes
        assert "odoo.hr.team" in scopes
        assert "odoo.hr.directory" in scopes
        assert "odoo.leave.read" in scopes
        assert "odoo.leave.write" in scopes
        assert "odoo.documents.read" in scopes
        assert "odoo.sign.read" in scopes

    def test_allowlisted_admin_gets_odoo_read_and_write(self):
        """A user whose email is explicitly on the allowlist gets full CRUD access."""
        claims = self._claims("amp@softgrouprd.com")

        context = extract_user_context(claims, crud_admin_emails={"amp@softgrouprd.com"})
        scopes = context["scopes"]

        assert "odoo.read" in scopes
        assert "odoo.write" in scopes

    def test_allowlist_match_is_case_insensitive(self):
        """Email casing in the token claim must not bypass or block the allowlist match."""
        claims = self._claims("AMP@SoftGroupRD.com")

        context = extract_user_context(claims, crud_admin_emails={"amp@softgrouprd.com"})
        scopes = context["scopes"]

        assert "odoo.read" in scopes
        assert "odoo.write" in scopes

    def test_no_allowlist_configured_means_nobody_gets_crud_access(self):
        """With crud_admin_emails=None (unset), nobody gets odoo.read/odoo.write automatically."""
        claims = self._claims("amp@softgrouprd.com")

        context = extract_user_context(claims, crud_admin_emails=None)
        scopes = context["scopes"]

        assert "odoo.read" not in scopes
        assert "odoo.write" not in scopes

    def test_unverified_email_never_gets_crud_access_even_if_allowlisted(self):
        """An unverified email must not get CRUD access even if it matches the allowlist."""
        claims = self._claims("amp@softgrouprd.com", email_verified=False)

        context = extract_user_context(claims, crud_admin_emails={"amp@softgrouprd.com"})
        scopes = context["scopes"]

        assert "odoo.read" not in scopes
        assert "odoo.write" not in scopes

    def test_internal_email_domain_alone_no_longer_grants_crud_write(self):
        """
        Regression guard for the vulnerability: internal_email_domain must no
        longer be sufficient, by itself, to grant odoo.write. Only explicit
        presence in crud_admin_emails should.
        """
        claims = self._claims("employee@softgrouprd.com")

        context = extract_user_context(
            claims,
            internal_email_domain="softgrouprd.com",
        )
        scopes = context["scopes"]

        assert "odoo.write" not in scopes
        assert "odoo.read" not in scopes


class TestEmployeeToolsStillAccessibleForNonAdmins:
    """
    Regression: employee self-service tools must remain reachable for a
    non-admin user via their specific scope, since check_scope_access() OR's
    the tool's specific scope with odoo.read/odoo.write.
    """

    def test_get_my_profile_reachable_without_odoo_read(self):
        claims = {
            "iss": "https://id.example-internal.com",
            "sub": "user-1",
            "email": "employee@softgrouprd.com",
            "email_verified": True,
            "scope": "openid email profile",
        }
        context = extract_user_context(claims, crud_admin_emails=set())
        scopes = context["scopes"]

        assert "odoo.read" not in scopes
        assert check_scope_access(TOOL_SCOPE_REQUIREMENTS["get_my_profile"], scopes) is True
        assert check_scope_access(TOOL_SCOPE_REQUIREMENTS["get_my_leave_balance"], scopes) is True
        assert check_scope_access(TOOL_SCOPE_REQUIREMENTS["get_my_pending_signatures"], scopes) is True

    def test_generic_crud_tools_unreachable_without_admin_allowlist(self):
        claims = {
            "iss": "https://id.example-internal.com",
            "sub": "user-1",
            "email": "employee@softgrouprd.com",
            "email_verified": True,
            "scope": "openid email profile",
        }
        context = extract_user_context(claims, crud_admin_emails=set())
        scopes = context["scopes"]

        assert check_scope_access(TOOL_SCOPE_REQUIREMENTS["search_records"], scopes) is False
        assert check_scope_access(TOOL_SCOPE_REQUIREMENTS["count_records"], scopes) is False
        assert check_scope_access(TOOL_SCOPE_REQUIREMENTS["get_record"], scopes) is False
        assert check_scope_access(TOOL_SCOPE_REQUIREMENTS["create_record"], scopes) is False
        assert check_scope_access(TOOL_SCOPE_REQUIREMENTS["update_record"], scopes) is False
        assert check_scope_access(TOOL_SCOPE_REQUIREMENTS["delete_record"], scopes) is False

    def test_generic_crud_tools_reachable_for_admin_allowlist_member(self):
        claims = {
            "iss": "https://id.example-internal.com",
            "sub": "user-1",
            "email": "amp@softgrouprd.com",
            "email_verified": True,
            "scope": "openid email profile",
        }
        context = extract_user_context(claims, crud_admin_emails={"amp@softgrouprd.com"})
        scopes = context["scopes"]

        assert check_scope_access(TOOL_SCOPE_REQUIREMENTS["search_records"], scopes) is True
        assert check_scope_access(TOOL_SCOPE_REQUIREMENTS["create_record"], scopes) is True
        assert check_scope_access(TOOL_SCOPE_REQUIREMENTS["delete_record"], scopes) is True
