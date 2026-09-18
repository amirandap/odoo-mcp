"""Search domain field extraction.

Odoo raises a raw ``ValueError`` deep inside XML-RPC when a search domain
references a field the model doesn't have (e.g. filtering ``active`` on a
model without that field) - that surfaces to MCP callers as an opaque
traceback instead of an actionable message. ``extract_domain_fields()``
pulls the field names a domain references so the caller can check them
against the model's real fields (``fields_get``) before ever hitting Odoo.
"""

from __future__ import annotations


def extract_domain_fields(domain: list) -> set[str]:
    """Return the top-level field names referenced by a search domain.

    Handles Odoo's polish-notation domains (``['&', ...]``/``['|', ...]``/
    ``['!', ...]`` followed by leaves) as well as plain leaves, given as
    either lists or tuples. Dotted paths (related fields, e.g.
    ``'partner_id.name'``) are reduced to their first segment, since that's
    the field ``fields_get()`` can confirm exists on this model.
    """
    fields: set[str] = set()
    for item in domain:
        if isinstance(item, str):
            continue  # logical operator ('&', '|', '!')
        if isinstance(item, (list, tuple)) and len(item) == 3:
            field = item[0]
            if isinstance(field, str):
                fields.add(field.split(".")[0])
    return fields
