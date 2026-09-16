"""s2s-mcp: a hosted MCP server composing acmadDL and africas2s for thin agents.

The two libraries each ship a thin MCP server for local use. This package is
the deployment: one endpoint, run by a regional centre, that a national
hydromet agency's agent (possibly a small, cheap model) can drive with a
handful of high-level tools. It adds recipes (standard workflows in one
call), a pipeline runner (chain any primitive without intermediate results
entering the agent's context), handle-based outputs, and tenancy plumbing.
It holds no data-access or forecasting logic of its own.
"""

from .server import mcp, main  # noqa: F401
