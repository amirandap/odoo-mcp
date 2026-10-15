"""
The container healthcheck hits GET /health every 30s; at INFO level that is
~2,880 useless access-log lines a day. The filter drops only those.

Run with: pytest tests/unit/test_health_log_filter.py -v -m unit
"""

import logging

import pytest

pytestmark = [pytest.mark.unit]


def _record(msg, args=()):
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, msg, args, None)


def test_health_access_line_is_dropped():
    from odoo_mcp_server.http_server import HealthCheckLogFilter

    record = _record('%s - "%s %s HTTP/%s" %d', ("127.0.0.1:1", "GET", "/health", "1.1", 200))
    assert HealthCheckLogFilter().filter(record) is False


def test_other_access_lines_are_kept():
    from odoo_mcp_server.http_server import HealthCheckLogFilter

    record = _record('%s - "%s %s HTTP/%s" %d', ("10.0.0.2:1", "POST", "/mcp", "1.1", 200))
    assert HealthCheckLogFilter().filter(record) is True


def test_health_lookalike_path_is_kept():
    from odoo_mcp_server.http_server import HealthCheckLogFilter

    record = _record('%s - "%s %s HTTP/%s" %d', ("10.0.0.2:1", "GET", "/healthy-things", "1.1", 200))
    assert HealthCheckLogFilter().filter(record) is True
