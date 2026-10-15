"""MCP Apps (SEP-1865) wiring: the outlook widget resource and tool metadata.

The widget is a single self-contained HTML file built from ``web/`` and
checked in at ``src/s2s_mcp/web/outlook.html``. It is served under the
``ui://`` scheme with the MCP Apps mime type; hosts that support apps render
it in a sandboxed iframe and push ``seasonal_outlook``'s result into it.
Hosts that do not simply see the tool's text. The ``Apps`` instance must be
fully populated before ``MCPServer(extensions=[apps])`` is constructed.
"""

from __future__ import annotations

import logging
from pathlib import Path

from mcp.server.apps import Apps

log = logging.getLogger(__name__)

OUTLOOK_URI = "ui://s2s/outlook"
WEB_DIR = Path(__file__).resolve().parent / "web"
OUTLOOK_HTML = WEB_DIR / "outlook.html"
IMAGE_URI = "ui://s2s/image"
IMAGE_HTML = WEB_DIR / "image.html"

# ChatGPT reads its own alias of _meta.ui.resourceUri (confirmed required as of
# 2026-10-09) and shows the invocation strings while the tool runs.
OUTLOOK_TOOL_META = {
    "openai/outputTemplate": OUTLOOK_URI,
    "openai/widgetAccessible": True,
    "openai/toolInvocation/invoking": "Running the seasonal pipeline…",
    "openai/toolInvocation/invoked": "Outlook ready",
}
IMAGE_TOOL_META = {
    "openai/outputTemplate": IMAGE_URI,
    "openai/widgetAccessible": True,
    "openai/toolInvocation/invoking": "Opening the map…",
    "openai/toolInvocation/invoked": "Map ready",
}
# Hidden from the model, callable by the widget through the host.
APP_ONLY_META = {"ui": {"visibility": ["app"]}}

_PLACEHOLDER = (
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>s2s outlook</title></head>"
    "<body><p>The s2s outlook widget is not built in this install: run <code>npm run build</code> "
    "in <code>web/</code>.</p></body></html>"
)


def widget_html(path: Path | None = None) -> str:
    path = path or OUTLOOK_HTML
    if path.is_file():
        return path.read_text(encoding="utf-8")
    log.warning("widget not found at %s; serving a placeholder", path)
    return _PLACEHOLDER


apps = Apps()
apps.add_html_resource(
    OUTLOOK_URI,
    widget_html(),
    name="outlook",
    title="Seasonal outlook",
    description="Interactive tercile-probability map rendered for seasonal_outlook results.",
    prefers_border=True,
)
apps.add_html_resource(
    IMAGE_URI,
    widget_html(IMAGE_HTML),
    name="map",
    title="Map",
    description="Shows a PNG output (a plot_field or plot_terciles map) inline for view_output results.",
    prefers_border=True,
)
