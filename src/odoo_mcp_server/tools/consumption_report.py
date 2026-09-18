"""
Monthly Consumption Report

Reports invoiced sales per client for a single Odoo company and month.
Ported from vivaldi_webai's src/lib/consumos/report.ts: the trustworthy
number is `account.move.line` on POSTED customer invoices, never a
`sale.order` total (which mixes draft/cancelled orders and, in a
multi-company Odoo, other companies' data - see the domain built in
`build_consumption_domain`).

The client list is NOT hardcoded: each report "grupo" (e.g. "avendra",
"quantum") maps to a native Odoo `res.partner.category` tag (Contact Tags).
Whoever owns the Odoo account adds/removes a hotel from the report by
tagging/untagging its contact - no redeploy needed.

Opt-in per deployment: only exposed when the "reports" tool group is
enabled and CONSUMPTION_REPORT_GROUPS is configured (see config.py).
"""
import base64
import calendar
import io
import json
import re
from typing import Any

import openpyxl
from mcp.types import TextContent, Tool
from openpyxl.styles import Font

from ..odoo.client import OdooClient
from ..odoo.exceptions import OdooValidationError

_PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")

TOOLS = [
    Tool(
        name="generate_consumption_report",
        description=(
            "Generate the monthly consumption report (invoiced sales per client, as an "
            "xlsx ready to send) for a configured client group, from posted customer "
            "invoices in Odoo. The client group's membership is a native Odoo contact "
            "tag, managed directly in Odoo"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "periodo": {
                    "type": "string",
                    "description": "Period to report, as YYYY-MM (e.g. '2026-08')",
                },
                "grupo": {
                    "type": "string",
                    "description": (
                        "Which configured report group to generate (e.g. 'avendra', 'quantum'). "
                        "Optional when only one group is configured on this deployment."
                    ),
                },
            },
            "required": ["periodo"],
        },
    )
]


def period_bounds(periodo: str) -> tuple[str, str]:
    """Return (first_day, last_day) of a 'YYYY-MM' period, as 'YYYY-MM-DD' strings."""
    if not _PERIOD_RE.match(periodo):
        raise OdooValidationError(f"Invalid periodo '{periodo}': expected format YYYY-MM")
    year, month = (int(p) for p in periodo.split("-"))
    last_day = calendar.monthrange(year, month)[1]
    return f"{periodo}-01", f"{periodo}-{last_day:02d}"


def build_consumption_domain(
    commercial_partner_id: int,
    inicio: str,
    fin: str,
    company_id: int,
) -> list:
    """Domain for invoiced product lines of one client, in one company, in one period."""
    return [
        ["move_id.move_type", "=", "out_invoice"],
        ["move_id.state", "=", "posted"],
        ["move_id.invoice_date", ">=", inicio],
        ["move_id.invoice_date", "<=", fin],
        ["partner_id.commercial_partner_id", "=", commercial_partner_id],
        ["display_type", "=", "product"],
        ["company_id", "=", company_id],
    ]


async def resolve_partners_by_tag(client: OdooClient, tag_name: str) -> list[dict]:
    """Return the partners (id, name, vat) carrying the given res.partner.category tag."""
    categories = await client.search_read(
        model="res.partner.category",
        domain=[["name", "=", tag_name]],
        fields=["id"],
        limit=1,
    )
    if not categories:
        raise OdooValidationError(f"No res.partner.category tag named '{tag_name}' found in Odoo")

    tag_id = categories[0]["id"]
    return await client.search_read(
        model="res.partner",
        domain=[["category_id", "in", [tag_id]]],
        fields=["id", "name", "vat"],
        limit=1000,
        order="name",
    )


async def _consumo_for_client(
    client: OdooClient,
    commercial_partner_id: int,
    inicio: str,
    fin: str,
    company_id: int,
) -> tuple[float, int]:
    domain = build_consumption_domain(commercial_partner_id, inicio, fin, company_id)
    lines = await client.search_read(
        model="account.move.line",
        domain=domain,
        fields=["price_subtotal"],
        limit=10000,
    )
    total = sum(line.get("price_subtotal") or 0 for line in lines)
    return total, len(lines)


def _build_xlsx_base64(rows: list[dict], periodo: str, grupo: str) -> str:
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None  # a freshly created Workbook always has an active sheet
    ws.title = f"Consumo {periodo}"[:31]
    headers = ["Cliente", "RNC", f"Consumo base imponible {periodo} (DOP)", "N° facturas"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append([row["cliente"], row["rnc"], row["consumoDop"], row["facturas"]])
    ws.column_dimensions["A"].width = 45
    ws.column_dimensions["B"].width = 15
    ws.column_dimensions["C"].width = 32
    ws.column_dimensions["D"].width = 12
    for amount_cell_row in ws.iter_rows(min_row=2, min_col=3, max_col=3):
        amount_cell_row[0].number_format = "#,##0.00"

    buffer = io.BytesIO()
    wb.save(buffer)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


async def execute_tool(
    name: str,
    arguments: dict[str, Any],
    client: OdooClient,
    report_groups: dict[str, str],
    company_id: int,
) -> list[TextContent]:
    """Execute a report tool and return results."""

    if name != "generate_consumption_report":
        raise ValueError(f"Unknown tool: {name}")

    if not report_groups:
        raise OdooValidationError(
            "No report groups configured. Set CONSUMPTION_REPORT_GROUPS to enable this tool."
        )

    grupo = arguments.get("grupo")
    if grupo is None:
        if len(report_groups) == 1:
            grupo = next(iter(report_groups))
        else:
            raise OdooValidationError(
                f"Specify 'grupo': one of {sorted(report_groups)}"
            )
    elif grupo not in report_groups:
        raise OdooValidationError(
            f"Unknown grupo '{grupo}'. Valid: {sorted(report_groups)}"
        )

    inicio, fin = period_bounds(arguments["periodo"])
    tag_name = report_groups[grupo]
    partners = await resolve_partners_by_tag(client, tag_name)

    rows: list[dict[str, Any]] = []
    for partner in partners:
        total, n = await _consumo_for_client(
            client,
            partner["id"],
            inicio,
            fin,
            company_id,
        )
        rows.append(
            {
                "cliente": partner.get("name"),
                "rnc": partner.get("vat") or "",
                "consumoDop": total,
                "facturas": n,
            }
        )

    periodo = arguments["periodo"]
    payload = {
        "periodo": periodo,
        "grupo": grupo,
        "rows": rows,
        "totalDop": sum(r["consumoDop"] for r in rows),
        "xlsx_filename": f"consumo-{grupo}-{periodo}.xlsx",
        "xlsx_base64": _build_xlsx_base64(rows, periodo, grupo),
    }
    return [TextContent(type="text", text=json.dumps(payload, default=str))]
