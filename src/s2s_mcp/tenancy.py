"""Who is calling, and where their files live.

The hosted server serves many agencies from one process. Every output is
written under a per-tenant directory and every handle is resolved against
the caller's tenant, so one agency cannot read another's files.

Tenant resolution modes (``S2S_TENANT_MODE``):

* ``single`` (default): everything is one tenant, ``"default"``. Right for a
  local run or a deployment that fronts a single organisation.
* ``header``: the tenant id is the ``X-S2S-Tenant`` request header. Use this
  behind an authenticating proxy that sets the header from the verified
  identity. The server does not verify it itself; that is the proxy's job,
  or a future OAuth ``token_verifier`` on the MCPServer.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError

_TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
HEADER = "x-s2s-tenant"


def output_root() -> Path:
    root = Path(os.environ.get("S2S_OUTPUT_ROOT") or (Path.home() / ".s2s-mcp" / "outputs")).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def tenant_mode() -> str:
    return os.environ.get("S2S_TENANT_MODE", "single")


def tenant_from_headers(headers) -> str:
    """Resolve the tenant id for a request. ``headers`` may be None (stdio)."""
    mode = tenant_mode()
    if mode == "single":
        return "default"
    if mode == "header":
        value = None
        if headers is not None:
            value = headers.get(HEADER) or headers.get(HEADER.title())
        if not value:
            raise ToolError(f"Missing {HEADER} header: this server runs in header tenant mode.")
        if not _TENANT_RE.match(value):
            raise ToolError(f"Invalid tenant id {value!r}.")
        return value
    raise ToolError(f"Unknown S2S_TENANT_MODE {mode!r}; expected 'single' or 'header'.")


def tenant_dir(tenant: str) -> Path:
    d = output_root() / tenant
    d.mkdir(parents=True, exist_ok=True)
    return d
