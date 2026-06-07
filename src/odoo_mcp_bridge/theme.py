"""OS-theme support for the bridge's HTML pages.

The only HTML the bridge serves is the OAuth consent page rendered by the ``fastmcp``
library (``Application Access Request`` / ``Allow Access``), which has no theming hook of
its own. Rather than fork the library, ``DarkModeMiddleware`` injects a single
``prefers-color-scheme`` stylesheet into every ``text/html`` response, right before
``</head>``. ``color-scheme: light dark`` lets native form controls follow the OS; the
``@media`` block restyles the page chrome. The injected ``<style>`` is inline, which the
consent page's CSP (``style-src 'unsafe-inline'``) already permits.

It must stay pure-ASGI (never ``BaseHTTPMiddleware``): the MCP endpoint streams responses,
and buffering those would break StreamableHTTP. Only ``text/html`` responses are
buffered/rewritten; everything else (incl. MCP streaming) is forwarded untouched.

Adapted from plane-mcp-bridge's theme.py.
"""

from __future__ import annotations

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Dark palette inspired by GitHub's dark theme; selectors cover the fastmcp consent page
# (.container/.info-box/.redirect-section/.detail-*/.btn-*/summary/.tooltip). !important
# overrides the page's inline <style>.
_DARK_MODE_CSS = """
:root { color-scheme: light dark; }
@media (prefers-color-scheme: dark) {
  body { background: #0d1117 !important; color: #e6edf3 !important; }
  .container { background: #161b22 !important; border-color: #30363d !important; }
  h1, h2 { color: #f0f6fc !important; }
  a, .server-name-link { color: #58a6ff !important; }
  code { background: #21262d !important; color: #e6edf3 !important; }
  .muted, .subtitle, .detail-label, summary, .help-link { color: #9da7b1 !important; }
  .ok { color: #3fb950 !important; }
  .err { color: #f85149 !important; }
  input, input[type=password] {
    background: #0d1117 !important; color: #e6edf3 !important; border: 1px solid #30363d !important;
  }
  .info-box { background: #15212b !important; border-color: #1f4d63 !important; color: #c9d1d9 !important; }
  .info-box-mono, .detail-box, .warning-box, details, summary {
    background: #0d1117 !important; border-color: #30363d !important;
  }
  .detail-value, .message { color: #e6edf3 !important; }
  .redirect-section { background: #2b2410 !important; border-color: #6b5316 !important; }
  .redirect-section .value { color: #f0f6fc !important; }
  .btn-deny { background: #30363d !important; color: #e6edf3 !important; }
  .tooltip { background: #161b22 !important; color: #e6edf3 !important; }
}
"""

_STYLE_TAG = f"<style>{_DARK_MODE_CSS}</style>".encode()


def _inject(body: bytes) -> bytes:
    """Insert the dark-mode stylesheet just before the first ``</head>`` (case-insensitive)."""
    idx = body.lower().find(b"</head>")
    if idx == -1:
        return body
    return body[:idx] + _STYLE_TAG + body[idx:]


class DarkModeMiddleware:
    """Inject an OS dark/light stylesheet into ``text/html`` responses; stream everything else."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_message: Message = {}
        chunks: list[bytes] = []
        rewriting = False

        async def send_wrapper(message: Message) -> None:
            nonlocal rewriting, start_message
            msg_type = message["type"]

            if msg_type == "http.response.start":
                headers = Headers(raw=message["headers"])
                content_type = headers.get("content-type", "")
                content_encoding = headers.get("content-encoding", "").lower()
                # Only rewrite uncompressed HTML; pass compressed/other bodies through untouched.
                if content_type.startswith("text/html") and content_encoding in ("", "identity"):
                    rewriting = True
                    start_message = message
                    return
                await send(message)
                return

            if msg_type == "http.response.body" and rewriting:
                chunks.append(message.get("body", b""))
                if message.get("more_body", False):
                    return
                body = _inject(b"".join(chunks))
                headers = MutableHeaders(raw=start_message["headers"])
                headers["content-length"] = str(len(body))
                await send(start_message)
                await send({"type": "http.response.body", "body": body, "more_body": False})
                return

            await send(message)

        await self.app(scope, receive, send_wrapper)
