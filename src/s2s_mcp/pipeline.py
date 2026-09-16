"""Run a chain of primitives on the server so only the final result reaches the agent.

This is the code-execution idea from Anthropic's "code execution with MCP"
essay, hosted server-side for agents that cannot run code: the agent submits
a list of steps, each naming a primitive from acmaddl or africas2s and its
arguments, with ``$step.field`` references to earlier outputs. Steps run
in-process through the two libraries' own MCP tool functions, files stay on
the host, and the agent sees one result.

No user code runs. The step vocabulary is exactly the primitives' schemas,
validated by the same code that validates a direct tool call.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from acmaddl.mcp import server as _acmaddl
from africas2s.mcp import server as _africas2s

from . import handles

# Primitives that make no sense inside a pipeline (job plumbing for direct callers).
_EXCLUDE = {"acmaddl.start_fetch", "acmaddl.fetch_status", "acmaddl.list_jobs"}

_REF_RE = re.compile(r"^\$([A-Za-z_][\w-]*)((?:\.[\w-]+)*)$")


@dataclass(frozen=True)
class Operation:
    name: str
    server: Any
    tool: str
    description: str
    input_schema: dict
    output_schema: dict | None
    accepts_destination: bool

    @property
    def summary(self) -> str:
        return self.description.strip().split("\n", 1)[0]


def _run_sync(coro):
    """Run a coroutine from sync code, even when a loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _discover(module, prefix: str) -> dict[str, Operation]:
    ops = {}
    for tool in _run_sync(module.mcp.list_tools()):
        name = f"{prefix}.{tool.name}"
        if name in _EXCLUDE:
            continue
        props = tool.input_schema.get("properties", {})
        ops[name] = Operation(
            name=name, server=module.mcp, tool=tool.name,
            description=tool.description or "",
            input_schema=tool.input_schema, output_schema=tool.output_schema,
            accepts_destination="destination" in props,
        )
    return ops


OPERATIONS: dict[str, Operation] = {
    **_discover(_acmaddl, "acmaddl"),
    **_discover(_africas2s, "africas2s"),
}


def operation(name: str) -> Operation:
    op = OPERATIONS.get(name)
    if op is None:
        raise ToolError(f"Unknown operation {name!r}. list_operations shows the vocabulary.")
    return op


# --------------------------------------------------------------------------
# argument resolution
# --------------------------------------------------------------------------

def _lookup(results: dict[str, Any], ref: str) -> Any:
    m = _REF_RE.match(ref)
    step, path = m.group(1), [p for p in m.group(2).split(".") if p]
    if step not in results:
        raise ToolError(f"Reference {ref!r} points at step {step!r}, which has not run yet.")
    value = results[step]
    for key in path:
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and key.isdigit():
            value = value[int(key)]
        else:
            raise ToolError(f"Reference {ref!r}: {key!r} not found in step {step!r}'s result "
                            f"(keys: {list(value)[:12] if isinstance(value, dict) else type(value).__name__}).")
    return value


def resolve_args(args: Any, results: dict[str, Any], tenant: str) -> Any:
    """Replace ``$step.field`` references and output handles inside ``args``."""
    if isinstance(args, dict):
        return {k: resolve_args(v, results, tenant) for k, v in args.items()}
    if isinstance(args, list):
        return [resolve_args(v, results, tenant) for v in args]
    if isinstance(args, str):
        if _REF_RE.match(args):
            return resolve_args(_lookup(results, args), results, tenant)
        if handles.is_handle(args):
            return str(handles.resolve(tenant, args))
    return args


# --------------------------------------------------------------------------
# result handling
# --------------------------------------------------------------------------

def handleize(result: Any, tenant: str, *, label: str, origin: dict | None = None) -> Any:
    """Swap host paths in a primitive's result for tenant-scoped handles."""
    if isinstance(result, list):
        return [handleize(v, tenant, label=label, origin=origin) for v in result]
    if not isinstance(result, dict):
        return result
    out = {}
    for key, value in result.items():
        if key == "path" and isinstance(value, str):
            kind = "image" if value.endswith(".png") else "dataset"
            h = handles.mint(tenant, value, kind=kind, label=label, origin=origin)
            out["handle"] = h["handle"]
            out["media_type"] = h["media_type"]
        elif key == "spatial_path" and isinstance(value, str):
            h = handles.mint(tenant, value, kind="dataset", label=f"{label} (spatial skill)", origin=origin)
            out["spatial_handle"] = h["handle"]
        elif isinstance(value, dict):
            out[key] = handleize(value, tenant, label=f"{label}.{key}", origin=origin)
        else:
            out[key] = value
    return out


def brief(result: Any) -> Any:
    """Trim a dataset summary to what a small agent needs to continue."""
    if not isinstance(result, dict):
        return result
    if "dims" in result and "variables" in result:
        out = {k: v for k, v in result.items()
               if k not in ("coords", "attrs", "size_bytes", "request", "variables")}
        out["dims"] = result["dims"]
        out["variables"] = {name: (v.get("units") if isinstance(v, dict) else None)
                            for name, v in result["variables"].items()}
        for key in ("pev", "spatial"):
            if key in result:
                out[key] = brief(result[key])
        return out
    return {k: brief(v) if isinstance(v, dict) else v for k, v in result.items()}


def _extract(call_result) -> Any:
    if call_result.structured_content is not None:
        return call_result.structured_content
    for block in call_result.content:
        if getattr(block, "type", None) == "text":
            try:
                return json.loads(block.text)
            except json.JSONDecodeError:
                return block.text
    return None


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------

async def run_step(op: Operation, args: dict, tenant: str, *, run_id: str, step_id: str) -> Any:
    args = dict(args)
    if op.accepts_destination and not args.get("destination"):
        # The primitive writes straight to a reserved handle path; nothing moves later.
        ext = ".png" if op.tool.startswith("plot_") else ".nc"
        _handle, path = handles.reserve(tenant, ext)
        args["destination"] = str(path)
    try:
        result = await op.server.call_tool(op.tool, args)
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"{type(exc).__name__}: {exc}") from exc
    if getattr(result, "is_error", False):
        text = result.content[0].text if result.content else "unknown error"
        raise ToolError(text)
    payload = _extract(result)
    return handleize(payload, tenant, label=f"{run_id}/{step_id} {op.name}",
                     origin={"run_id": run_id, "step": step_id, "op": op.name, "args": _safe(args)})


def _safe(args: dict) -> dict:
    return {k: v for k, v in args.items() if k != "destination"}


async def run_pipeline(steps: list[dict], tenant: str, *, keep: list[str] | None = None,
                       verbosity: str = "brief") -> dict:
    """Execute ``steps`` in order; return the last result plus any ``keep`` steps."""
    if not steps:
        raise ToolError("steps must not be empty.")
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    results: dict[str, Any] = {}
    trace: list[dict] = []
    seen: set[str] = set()
    for index, step in enumerate(steps):
        step_id = str(step.get("id") or f"s{index + 1}")
        if step_id in seen:
            raise ToolError(f"Duplicate step id {step_id!r}.")
        seen.add(step_id)
        op_name = step.get("op")
        if not op_name:
            raise ToolError(f"Step {step_id!r} has no 'op'.")
        op = operation(op_name)
        try:
            args = resolve_args(step.get("args") or {}, results, tenant)
            result = await run_step(op, args, tenant, run_id=run_id, step_id=step_id)
        except ToolError as exc:
            done = [f"{t['id']} -> {t.get('handle')}" for t in trace if t.get("handle")]
            raise ToolError(
                f"Step {step_id!r} ({op_name}) failed: {exc}. "
                f"Completed steps kept their outputs: {done or 'none'}."
            ) from exc
        results[step_id] = result
        trace.append({"id": step_id, "op": op_name, "status": "ok",
                      "handle": result.get("handle") if isinstance(result, dict) else None})
    final_id = trace[-1]["id"]
    shrink = brief if verbosity == "brief" else (lambda x: x)
    out = {"run_id": run_id, "steps": trace, "result": shrink(results[final_id])}
    if keep:
        missing = [k for k in keep if k not in results]
        if missing:
            raise ToolError(f"keep names unknown steps: {missing}.")
        out["kept"] = {k: shrink(results[k]) for k in keep if k != final_id}
    return out
