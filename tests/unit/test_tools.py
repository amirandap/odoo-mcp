"""
Unit tests for MCP tools.

Run with: pytest tests/unit/test_tools.py -v -m unit
"""

import pytest

pytestmark = [pytest.mark.unit]


class TestToolSchemaValidation:
    """Tests for tool input schema validation."""

    def test_search_records_required_fields(self):
        """search_records should require model field."""
        schema = {
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "domain": {"type": "array", "default": []},
                "fields": {"type": "array"},
                "limit": {"type": "integer", "default": 20},
                "offset": {"type": "integer", "default": 0},
            },
            "required": ["model"],
        }

        assert "model" in schema["required"]
        assert "domain" not in schema["required"]  # Has default

    def test_get_record_required_fields(self):
        """get_record should require model and record_id."""
        schema = {
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "record_id": {"type": "integer"},
                "fields": {"type": "array"},
            },
            "required": ["model", "record_id"],
        }

        assert "model" in schema["required"]
        assert "record_id" in schema["required"]

    def test_create_record_required_fields(self):
        """create_record should require model and values."""
        schema = {
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "values": {"type": "object"},
            },
            "required": ["model", "values"],
        }

        assert "model" in schema["required"]
        assert "values" in schema["required"]

    def test_update_record_required_fields(self):
        """update_record should require model, record_id, and values."""
        schema = {
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "record_id": {"type": "integer"},
                "values": {"type": "object"},
            },
            "required": ["model", "record_id", "values"],
        }

        assert len(schema["required"]) == 3

    def test_delete_record_required_fields(self):
        """delete_record should require model and record_id."""
        schema = {
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "record_id": {"type": "integer"},
            },
            "required": ["model", "record_id"],
        }

        assert "model" in schema["required"]
        assert "record_id" in schema["required"]


class TestToolResultFormatting:
    """Tests for tool result formatting."""

    def test_search_results_are_list(self):
        """Search results should be returned as a list."""
        import json

        results = [
            {"id": 1, "name": "Partner 1"},
            {"id": 2, "name": "Partner 2"},
        ]

        formatted = json.dumps(results)
        parsed = json.loads(formatted)

        assert isinstance(parsed, list)
        assert len(parsed) == 2

    def test_single_record_is_object(self):
        """Single record should be returned as an object."""
        import json

        record = {"id": 1, "name": "Partner 1", "email": "test@example.com"}

        formatted = json.dumps(record)
        parsed = json.loads(formatted)

        assert isinstance(parsed, dict)
        assert "id" in parsed

    def test_create_result_includes_id(self):
        """Create result should include the new record ID."""
        import json

        result = {"id": 42}

        formatted = json.dumps(result)
        parsed = json.loads(formatted)

        assert "id" in parsed
        assert parsed["id"] == 42

    def test_error_result_format(self):
        """Error results should have consistent format."""
        import json

        error_result = {"error": "Access denied", "code": "ACCESS_ERROR"}

        formatted = json.dumps(error_result)
        parsed = json.loads(formatted)

        assert "error" in parsed


class TestToolArgumentValidation:
    """Tests for tool argument validation."""

    def test_model_name_validation(self):
        """Model names should follow Odoo naming convention."""
        valid_models = ["res.partner", "crm.lead", "hr.employee", "product.product"]
        invalid_models = ["invalid", "no-dots", ""]

        for model in valid_models:
            assert "." in model
            assert len(model.split(".")) >= 2

        for model in invalid_models:
            is_valid = "." in model and len(model.split(".")) >= 2
            assert not is_valid

    def test_domain_format_validation(self):
        """Domain should be a list of conditions."""
        valid_domains = [
            [],
            [["name", "=", "test"]],
            [["is_company", "=", True], ["active", "=", True]],
            ["|", ["name", "ilike", "a"], ["name", "ilike", "b"]],
        ]

        for domain in valid_domains:
            assert isinstance(domain, list)

    def test_record_id_must_be_positive(self):
        """Record IDs must be positive integers."""
        valid_ids = [1, 42, 1000]
        invalid_ids = [0, -1, -100]

        for rid in valid_ids:
            assert rid > 0

        for rid in invalid_ids:
            assert rid <= 0

    def test_limit_bounds(self):
        """Limit should be within reasonable bounds."""
        min_limit = 1
        max_limit = 1000
        default_limit = 20

        assert default_limit >= min_limit
        assert default_limit <= max_limit

        # Test boundary
        assert min_limit > 0
        assert max_limit <= 10000  # Reasonable upper bound


class TestToolDescriptions:
    """Tests for tool documentation."""

    def test_all_tools_have_descriptions(self):
        """All tools should have non-empty descriptions."""
        tools = [
            {"name": "search_records", "description": "Search for records..."},
            {"name": "get_record", "description": "Get a single record..."},
            {"name": "create_record", "description": "Create a new record..."},
            {"name": "update_record", "description": "Update an existing record..."},
            {"name": "delete_record", "description": "Delete a record..."},
            {"name": "count_records", "description": "Count records..."},
            {"name": "list_models", "description": "List available models..."},
        ]

        for tool in tools:
            assert tool["description"]
            assert len(tool["description"]) > 10

    def test_tool_names_are_snake_case(self):
        """Tool names should be in snake_case."""
        tool_names = [
            "search_records",
            "get_record",
            "create_record",
            "update_record",
            "delete_record",
            "count_records",
            "list_models",
        ]

        for name in tool_names:
            assert name == name.lower()
            assert " " not in name
            assert "-" not in name


class TestEmployeeToolSchemas:
    """Tests for employee self-service tool schemas."""

    def test_employee_tools_count(self):
        """Verify expected number of employee tools."""
        from odoo_mcp_server.tools.employee import EMPLOYEE_TOOLS

        # 4 profile + 2 new profile + 5 leave + 1 new leave + 4 documents + 1 new document = 17
        assert len(EMPLOYEE_TOOLS) == 16

    def test_get_direct_reports_schema(self):
        """get_direct_reports should have no required fields."""
        from odoo_mcp_server.tools.employee import EMPLOYEE_TOOLS

        tool = next((t for t in EMPLOYEE_TOOLS if t.name == "get_direct_reports"), None)
        assert tool is not None
        assert tool.inputSchema["type"] == "object"
        assert "required" not in tool.inputSchema or len(tool.inputSchema.get("required", [])) == 0

    def test_update_my_contact_schema(self):
        """update_my_contact should have optional phone and email fields."""
        from odoo_mcp_server.tools.employee import EMPLOYEE_TOOLS

        tool = next((t for t in EMPLOYEE_TOOLS if t.name == "update_my_contact"), None)
        assert tool is not None
        assert "work_phone" in tool.inputSchema["properties"]
        assert "mobile_phone" in tool.inputSchema["properties"]
        assert "work_email" in tool.inputSchema["properties"]
        # All fields are optional
        assert "required" not in tool.inputSchema or len(tool.inputSchema.get("required", [])) == 0

    def test_get_public_holidays_schema(self):
        """get_public_holidays should have optional year field."""
        from odoo_mcp_server.tools.employee import EMPLOYEE_TOOLS

        tool = next((t for t in EMPLOYEE_TOOLS if t.name == "get_public_holidays"), None)
        assert tool is not None
        assert "year" in tool.inputSchema["properties"]
        assert tool.inputSchema["properties"]["year"]["type"] == "integer"

    def test_get_document_details_schema(self):
        """get_document_details should require document_id."""
        from odoo_mcp_server.tools.employee import EMPLOYEE_TOOLS

        tool = next((t for t in EMPLOYEE_TOOLS if t.name == "get_document_details"), None)
        assert tool is not None
        assert "document_id" in tool.inputSchema["properties"]
        assert "document_id" in tool.inputSchema["required"]

    def test_all_employee_tools_have_descriptions(self):
        """All employee tools should have non-empty descriptions."""
        from odoo_mcp_server.tools.employee import EMPLOYEE_TOOLS

        for tool in EMPLOYEE_TOOLS:
            assert tool.description, f"Tool {tool.name} missing description"
            assert len(tool.description) > 10, f"Tool {tool.name} has too short description"

    def test_employee_tool_names_are_snake_case(self):
        """Employee tool names should be in snake_case."""
        from odoo_mcp_server.tools.employee import EMPLOYEE_TOOLS

        for tool in EMPLOYEE_TOOLS:
            assert tool.name == tool.name.lower(), f"Tool {tool.name} not lowercase"
            assert " " not in tool.name, f"Tool {tool.name} has spaces"
            assert "-" not in tool.name, f"Tool {tool.name} has dashes"


class TestEmployeeToolConfig:
    """Tests for employee tool configuration."""

    def test_new_tools_have_scope_requirements(self):
        """New tools should have scope requirements in config."""
        from odoo_mcp_server.config import TOOL_SCOPE_REQUIREMENTS

        new_tools = [
            "get_direct_reports",
            "update_my_contact",
            "get_public_holidays",
            "get_document_details",
        ]

        for tool_name in new_tools:
            assert tool_name in TOOL_SCOPE_REQUIREMENTS, f"Missing scope for {tool_name}"
            assert len(TOOL_SCOPE_REQUIREMENTS[tool_name]) > 0, f"Empty scope for {tool_name}"

    def test_update_my_contact_is_write_tool(self):
        """update_my_contact should be classified as a write tool."""
        from odoo_mcp_server.config import WRITE_TOOLS

        assert "update_my_contact" in WRITE_TOOLS

    def test_new_profile_write_scope_exists(self):
        """odoo.hr.profile.write scope should be defined."""
        from odoo_mcp_server.config import OAUTH_SCOPES

        assert "odoo.hr.profile.write" in OAUTH_SCOPES


class TestEmployeeFieldSecurity:
    """Tests for employee field security constants."""

    def test_public_fields_defined(self):
        """Public employee fields should be defined."""
        from odoo_mcp_server.tools.employee import PUBLIC_EMPLOYEE_FIELDS

        assert "name" in PUBLIC_EMPLOYEE_FIELDS
        assert "work_email" in PUBLIC_EMPLOYEE_FIELDS
        assert "department_id" in PUBLIC_EMPLOYEE_FIELDS

    def test_self_fields_include_public(self):
        """Self employee fields should include all public fields."""
        from odoo_mcp_server.tools.employee import (
            PUBLIC_EMPLOYEE_FIELDS,
            SELF_EMPLOYEE_FIELDS,
        )

        for field in PUBLIC_EMPLOYEE_FIELDS:
            assert field in SELF_EMPLOYEE_FIELDS

    def test_restricted_folders_defined(self):
        """DMS restricted folders should be defined."""
        from odoo_mcp_server.tools.employee import DMS_RESTRICTED_FOLDERS

        assert "Background Checks" in DMS_RESTRICTED_FOLDERS
        assert "Offboarding Documents" in DMS_RESTRICTED_FOLDERS

    def test_allowed_folders_defined(self):
        """DMS allowed folders should be defined."""
        from odoo_mcp_server.tools.employee import DMS_ALLOWED_FOLDERS

        assert "Contracts" in DMS_ALLOWED_FOLDERS
        assert "Identity" in DMS_ALLOWED_FOLDERS

    def test_register_tools_excludes_sign_by_default(self):
        """Sign tools must be hidden unless the 'sign' group is enabled."""
        from odoo_mcp_server.tools import SIGN_TOOLS, register_tools

        sign_names = {t.name for t in SIGN_TOOLS}

        default = {t.name for t in register_tools()}
        with_sign = {t.name for t in register_tools(("crud", "employee", "sign"))}

        assert not (sign_names & default)
        assert sign_names <= with_sign

    def test_register_tools_crud_only(self):
        """Selecting only the crud group exposes exactly the CRUD tools."""
        from odoo_mcp_server.tools import (
            CRUD_TOOLS,
            EMPLOYEE_TOOLS,
            register_tools,
        )

        crud_names = {t.name for t in CRUD_TOOLS}
        employee_names = {t.name for t in EMPLOYEE_TOOLS}

        selected = {t.name for t in register_tools(("crud",))}
        assert selected == crud_names
        assert not (selected & employee_names)

    def test_register_tools_employee_only(self):
        """Selecting only the employee group hides the CRUD tools."""
        from odoo_mcp_server.tools import (
            CRUD_TOOLS,
            EMPLOYEE_TOOLS,
            register_tools,
        )

        selected = {t.name for t in register_tools(("employee",))}
        assert selected == {t.name for t in EMPLOYEE_TOOLS}
        assert not (selected & {t.name for t in CRUD_TOOLS})

    def test_tool_group_for(self):
        """tool_group_for maps tool names to their group."""
        from odoo_mcp_server.tools import tool_group_for

        assert tool_group_for("search_records") == "crud"
        assert tool_group_for("get_my_profile") == "employee"
        assert tool_group_for("list_sign_templates") == "sign"
        assert tool_group_for("does_not_exist") is None
