"""
Unit tests for Sign module MCP tools.

Run with: pytest tests/unit/test_sign_tools.py -v -m unit
"""

import pytest

from odoo_mcp_server.config import OAUTH_SCOPES, TOOL_SCOPE_REQUIREMENTS, WRITE_TOOLS
from odoo_mcp_server.oauth.resource_server import extract_user_context
from odoo_mcp_server.tools import execute_tool, register_employee_tools, register_tools
from odoo_mcp_server.tools.sign import SIGN_TOOLS

pytestmark = [pytest.mark.unit]

# All sign tool names for reuse across test classes
SIGN_TOOL_NAMES = [
    "get_my_pending_signatures",
    "get_my_signature_requests",
    "get_signature_request_status",
    "list_sign_templates",
    "send_signature_request",
    "download_signed_document",
    "cancel_signature_request",
]


class TestSignToolSchemaValidation:
    """Tests for sign tool input schema validation."""

    def test_sign_tools_count(self):
        """There should be exactly 7 sign tools."""
        assert len(SIGN_TOOLS) == 7

    def test_all_sign_tools_have_name(self):
        """Every sign tool should have a non-empty name."""
        for tool in SIGN_TOOLS:
            assert tool.name, f"Sign tool missing name: {tool}"
            assert len(tool.name) > 0

    def test_all_sign_tools_have_description(self):
        """Every sign tool should have a non-empty description."""
        for tool in SIGN_TOOLS:
            assert tool.description, f"Tool {tool.name} missing description"
            assert len(tool.description) > 10, f"Tool {tool.name} has too short description"

    def test_all_sign_tools_have_input_schema(self):
        """Every sign tool should have an inputSchema with type 'object'."""
        for tool in SIGN_TOOLS:
            assert tool.inputSchema is not None, f"Tool {tool.name} missing inputSchema"
            assert tool.inputSchema["type"] == "object", f"Tool {tool.name} schema type is not 'object'"

    def test_get_signature_request_status_requires_request_id(self):
        """get_signature_request_status should require request_id."""
        tool = next(t for t in SIGN_TOOLS if t.name == "get_signature_request_status")
        assert "request_id" in tool.inputSchema["required"]
        assert tool.inputSchema["properties"]["request_id"]["type"] == "integer"

    def test_send_signature_request_requires_template_id_and_signers(self):
        """send_signature_request should require template_id and signers."""
        tool = next(t for t in SIGN_TOOLS if t.name == "send_signature_request")
        assert "template_id" in tool.inputSchema["required"]
        assert "signers" in tool.inputSchema["required"]
        assert tool.inputSchema["properties"]["template_id"]["type"] == "integer"
        assert tool.inputSchema["properties"]["signers"]["type"] == "array"

    def test_download_signed_document_requires_request_id(self):
        """download_signed_document should require request_id."""
        tool = next(t for t in SIGN_TOOLS if t.name == "download_signed_document")
        assert "request_id" in tool.inputSchema["required"]
        assert tool.inputSchema["properties"]["request_id"]["type"] == "integer"

    def test_cancel_signature_request_requires_request_id(self):
        """cancel_signature_request should require request_id."""
        tool = next(t for t in SIGN_TOOLS if t.name == "cancel_signature_request")
        assert "request_id" in tool.inputSchema["required"]
        assert tool.inputSchema["properties"]["request_id"]["type"] == "integer"

    def test_get_my_signature_requests_status_enum(self):
        """get_my_signature_requests status field should have correct enum values."""
        tool = next(t for t in SIGN_TOOLS if t.name == "get_my_signature_requests")
        status_prop = tool.inputSchema["properties"]["status"]
        expected_enum = ["all", "draft", "sent", "signed", "canceled", "refused"]
        assert status_prop["enum"] == expected_enum
        assert status_prop["type"] == "string"

    def test_get_my_pending_signatures_has_no_required_fields(self):
        """get_my_pending_signatures should have no required fields."""
        tool = next(t for t in SIGN_TOOLS if t.name == "get_my_pending_signatures")
        assert "required" not in tool.inputSchema or len(tool.inputSchema.get("required", [])) == 0

    def test_list_sign_templates_has_no_required_fields(self):
        """list_sign_templates should have no required fields."""
        tool = next(t for t in SIGN_TOOLS if t.name == "list_sign_templates")
        assert "required" not in tool.inputSchema or len(tool.inputSchema.get("required", [])) == 0

    def test_sign_tool_names_are_snake_case(self):
        """Sign tool names should be in snake_case."""
        for tool in SIGN_TOOLS:
            assert tool.name == tool.name.lower(), f"Tool {tool.name} not lowercase"
            assert " " not in tool.name, f"Tool {tool.name} has spaces"
            assert "-" not in tool.name, f"Tool {tool.name} has dashes"


class TestSignToolConfig:
    """Tests for sign tool configuration in config.py."""

    def test_all_sign_tools_have_scope_requirements(self):
        """All 7 sign tools should have entries in TOOL_SCOPE_REQUIREMENTS."""
        for tool_name in SIGN_TOOL_NAMES:
            assert tool_name in TOOL_SCOPE_REQUIREMENTS, f"Missing scope requirement for {tool_name}"
            assert len(TOOL_SCOPE_REQUIREMENTS[tool_name]) > 0, f"Empty scope for {tool_name}"

    def test_sign_read_tools_map_to_read_scopes(self):
        """Sign read tools should map to odoo.sign.read and odoo.read scopes."""
        read_tools = [
            "get_my_pending_signatures",
            "get_my_signature_requests",
            "get_signature_request_status",
            "list_sign_templates",
            "download_signed_document",
        ]
        for tool_name in read_tools:
            scopes = TOOL_SCOPE_REQUIREMENTS[tool_name]
            assert "odoo.sign.read" in scopes, f"Tool {tool_name} missing odoo.sign.read scope"
            assert "odoo.read" in scopes, f"Tool {tool_name} missing odoo.read scope"

    def test_sign_write_tools_map_to_write_scopes(self):
        """Sign write tools should map to odoo.sign.write and odoo.write scopes."""
        write_tools = [
            "send_signature_request",
            "cancel_signature_request",
        ]
        for tool_name in write_tools:
            scopes = TOOL_SCOPE_REQUIREMENTS[tool_name]
            assert "odoo.sign.write" in scopes, f"Tool {tool_name} missing odoo.sign.write scope"
            assert "odoo.write" in scopes, f"Tool {tool_name} missing odoo.write scope"

    def test_send_signature_request_in_write_tools(self):
        """send_signature_request should be in WRITE_TOOLS list."""
        assert "send_signature_request" in WRITE_TOOLS

    def test_cancel_signature_request_in_write_tools(self):
        """cancel_signature_request should be in WRITE_TOOLS list."""
        assert "cancel_signature_request" in WRITE_TOOLS

    def test_sign_read_scope_exists_in_oauth_scopes(self):
        """odoo.sign.read should exist in OAUTH_SCOPES."""
        assert "odoo.sign.read" in OAUTH_SCOPES
        assert len(OAUTH_SCOPES["odoo.sign.read"]) > 0

    def test_sign_write_scope_exists_in_oauth_scopes(self):
        """odoo.sign.write should exist in OAUTH_SCOPES."""
        assert "odoo.sign.write" in OAUTH_SCOPES
        assert len(OAUTH_SCOPES["odoo.sign.write"]) > 0


class TestSignToolRegistration:
    """Tests for sign tool registration in the tools module."""

    def test_sign_tools_in_register_tools_when_group_enabled(self):
        """Sign tools should appear in register_tools() when the 'sign' group is enabled."""
        all_tools = register_tools(("crud", "employee", "sign"))
        all_tool_names = [t.name for t in all_tools]
        for tool_name in SIGN_TOOL_NAMES:
            assert tool_name in all_tool_names, f"Sign tool {tool_name} not in register_tools()"

    def test_sign_tools_excluded_by_default(self):
        """Sign tools must NOT appear in the default register_tools() output."""
        default_names = {t.name for t in register_tools()}
        for tool_name in SIGN_TOOL_NAMES:
            assert tool_name not in default_names

    def test_sign_tools_in_register_employee_tools(self):
        """Sign tools should appear in register_employee_tools() when 'sign' is enabled."""
        employee_tools = register_employee_tools(("employee", "sign"))
        employee_tool_names = [t.name for t in employee_tools]
        for tool_name in SIGN_TOOL_NAMES:
            assert tool_name in employee_tool_names, f"Sign tool {tool_name} not in register_employee_tools()"

    def test_sign_tool_names_are_unique(self):
        """All sign tool names should be unique and not conflict with employee/CRUD tools."""
        all_tools = register_tools()
        all_tool_names = [t.name for t in all_tools]
        # Check no duplicates in the combined list
        assert len(all_tool_names) == len(set(all_tool_names)), (
            f"Duplicate tool names found: "
            f"{[n for n in all_tool_names if all_tool_names.count(n) > 1]}"
        )

    @pytest.mark.asyncio
    async def test_execute_tool_raises_for_sign_tools(self):
        """execute_tool should raise ValueError for sign tools (require employee context)."""
        for tool_name in SIGN_TOOL_NAMES:
            with pytest.raises(ValueError, match="requires employee context"):
                await execute_tool(tool_name, {}, None)


class TestSignToolOAuthScopes:
    """Tests for OAuth scope grants related to sign tools."""

    def test_verified_google_user_gets_sign_read_scope(self):
        """extract_user_context should grant odoo.sign.read for verified Google users."""
        claims = {
            "iss": "https://accounts.google.com",
            "sub": "123456",
            "email": "user@external.com",
            "email_verified": True,
        }
        context = extract_user_context(claims)
        assert "odoo.sign.read" in context["scopes"]

    def test_unverified_google_user_does_not_get_sign_read_scope(self):
        """extract_user_context should not grant odoo.sign.read for unverified Google users."""
        claims = {
            "iss": "https://accounts.google.com",
            "sub": "123456",
            "email": "user@external.com",
            "email_verified": False,
        }
        context = extract_user_context(claims)
        assert "odoo.sign.read" not in context["scopes"]

    def test_internal_domain_user_gets_sign_write_scope(self):
        """extract_user_context should grant odoo.sign.write for internal domain users."""
        claims = {
            "iss": "https://accounts.google.com",
            "sub": "123456",
            "email": "employee@example.com",
            "email_verified": True,
        }
        context = extract_user_context(claims, internal_email_domain="example.com")
        assert "odoo.sign.write" in context["scopes"]
        assert "odoo.sign.read" in context["scopes"]

    def test_external_user_does_not_get_sign_write_scope(self):
        """extract_user_context should not grant odoo.sign.write for external domain users."""
        claims = {
            "iss": "https://accounts.google.com",
            "sub": "123456",
            "email": "user@external.com",
            "email_verified": True,
        }
        context = extract_user_context(claims, internal_email_domain="example.com")
        assert "odoo.sign.write" not in context["scopes"]
        # Should still have read scope
        assert "odoo.sign.read" in context["scopes"]

    def test_internal_user_without_domain_config_no_write_scope(self):
        """Without internal_email_domain configured, no user gets sign write scope."""
        claims = {
            "iss": "https://accounts.google.com",
            "sub": "123456",
            "email": "employee@example.com",
            "email_verified": True,
        }
        context = extract_user_context(claims, internal_email_domain=None)
        assert "odoo.sign.write" not in context["scopes"]
        assert "odoo.sign.read" in context["scopes"]
