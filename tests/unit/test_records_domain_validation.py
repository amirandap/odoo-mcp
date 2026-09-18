"""
Unit tests for search-domain field validation in the generic CRUD tools.

Odoo raises an opaque XML-RPC traceback (ValueError deep in the stack) when
a search domain references a field the model doesn't have - e.g. filtering
``active`` on ``sale.order.line``, which has no such field. These tests
cover validating the domain against the model's real fields (via
``fields_get``) before ever calling Odoo, so the caller gets a clear,
actionable MCP error instead.

Run with: pytest tests/unit/test_records_domain_validation.py -v -m unit
"""

from unittest.mock import AsyncMock

import pytest

from odoo_mcp_server.odoo.exceptions import OdooValidationError

pytestmark = [pytest.mark.unit]


class TestExtractDomainFields:
    """Tests for the pure domain-parsing helper."""

    def test_empty_domain_has_no_fields(self):
        from odoo_mcp_server.odoo.domain_validation import extract_domain_fields

        assert extract_domain_fields([]) == set()

    def test_plain_leaf(self):
        from odoo_mcp_server.odoo.domain_validation import extract_domain_fields

        assert extract_domain_fields([["active", "=", True]]) == {"active"}

    def test_multiple_leaves_with_logical_operators(self):
        from odoo_mcp_server.odoo.domain_validation import extract_domain_fields

        domain = ["&", ["active", "=", True], ["partner_id", "!=", False]]
        assert extract_domain_fields(domain) == {"active", "partner_id"}

    def test_tuple_leaves(self):
        """Odoo domains are commonly written with tuples, not just lists."""
        from odoo_mcp_server.odoo.domain_validation import extract_domain_fields

        assert extract_domain_fields([("state", "=", "sale")]) == {"state"}

    def test_dotted_related_field_reduced_to_first_segment(self):
        from odoo_mcp_server.odoo.domain_validation import extract_domain_fields

        domain = [["partner_id.name", "ilike", "Hamaca"]]
        assert extract_domain_fields(domain) == {"partner_id"}


class TestSearchRecordsDomainValidation:
    """Tests for search_records/count_records rejecting unknown domain fields."""

    @pytest.fixture
    def mock_odoo_client(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_search_records_rejects_unknown_field(self, mock_odoo_client):
        """A domain referencing a nonexistent field should fail fast with a
        clear message instead of reaching Odoo."""
        from odoo_mcp_server.tools.records import execute_tool

        mock_odoo_client.fields_get.return_value = {
            "id": {"string": "ID"},
            "order_id": {"string": "Order Reference"},
        }

        with pytest.raises(OdooValidationError) as exc_info:
            await execute_tool(
                "search_records",
                {
                    "model": "sale.order.line",
                    "domain": [["active", "=", True]],
                },
                mock_odoo_client,
            )

        assert "active" in str(exc_info.value)
        assert "sale.order.line" in str(exc_info.value)
        mock_odoo_client.search_read.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_records_allows_known_field(self, mock_odoo_client):
        from odoo_mcp_server.tools.records import execute_tool

        mock_odoo_client.fields_get.return_value = {
            "id": {"string": "ID"},
            "partner_id": {"string": "Customer"},
        }
        mock_odoo_client.search_read.return_value = [{"id": 1}]

        await execute_tool(
            "search_records",
            {"model": "sale.order", "domain": [["partner_id", "=", 8727]]},
            mock_odoo_client,
        )

        mock_odoo_client.search_read.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_domain_skips_validation(self, mock_odoo_client):
        """No domain filters means nothing to validate - and no extra
        fields_get round trip."""
        from odoo_mcp_server.tools.records import execute_tool

        mock_odoo_client.search_read.return_value = []

        await execute_tool(
            "search_records",
            {"model": "res.partner", "domain": []},
            mock_odoo_client,
        )

        mock_odoo_client.fields_get.assert_not_called()
        mock_odoo_client.search_read.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_count_records_rejects_unknown_field(self, mock_odoo_client):
        from odoo_mcp_server.tools.records import execute_tool

        mock_odoo_client.fields_get.return_value = {"id": {"string": "ID"}}

        with pytest.raises(OdooValidationError):
            await execute_tool(
                "count_records",
                {"model": "sale.order.line", "domain": [["active", "=", True]]},
                mock_odoo_client,
            )

        mock_odoo_client.search_count.assert_not_called()

    @pytest.mark.asyncio
    async def test_dotted_relation_field_not_flagged(self, mock_odoo_client):
        """'partner_id.name' should validate 'partner_id' against the
        model's fields, not the literal dotted string."""
        from odoo_mcp_server.tools.records import execute_tool

        mock_odoo_client.fields_get.return_value = {
            "id": {"string": "ID"},
            "partner_id": {"string": "Customer"},
        }
        mock_odoo_client.search_read.return_value = []

        await execute_tool(
            "search_records",
            {"model": "sale.order", "domain": [["partner_id.name", "ilike", "Hamaca"]]},
            mock_odoo_client,
        )

        mock_odoo_client.search_read.assert_awaited_once()
