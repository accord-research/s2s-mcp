"""Server-minted handles for outputs.

Remote agents cannot read host paths, and MCP 2026-07-28 wants cross-call
state carried as explicit server-minted handles. Every file a tool produces
gets an opaque id, ``out_<hex>``; tools accept handles wherever they accept
paths, and ``get_output`` turns a handle into content (an image inline, or a
download link for data files).

The store is the filesystem: ``<output_root>/<tenant>/<id>.<ext>`` plus a
sidecar ``<id>.json`` with the label, kind, and origin. No database, so a
stateless HTTP deployment with several replicas works as long as they share
the volume.

Preferred flow: ``reserve`` a handle, have the primitive write straight to
its path, then ``register`` it. Files are never moved after a library wrote
them (xarray's file cache can still reference the old path). ``mint`` copies
a file the caller did not place, for secondary outputs a primitive names
itself.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

from mcp.server.mcpserver.exceptions import ToolError

from .tenancy import tenant_dir

_HANDLE_RE = re.compile(r"^out_[0-9a-f]{12}$")

_MEDIA = {
    ".nc": "application/x-netcdf",
    ".png": "image/png",
    ".json": "application/json",
    ".csv": "text/csv",
}


class Handle(TypedDict):
    handle: str
    kind: str
    label: str
    media_type: str
    size_bytes: int


def is_handle(value: Any) -> bool:
    return isinstance(value, str) and bool(_HANDLE_RE.match(value))


def reserve(tenant: str, suffix: str) -> tuple[str, Path]:
    """Mint a fresh handle id and the path a producer should write to."""
    handle = f"out_{uuid.uuid4().hex[:12]}"
    return handle, tenant_dir(tenant) / f"{handle}{suffix}"


def register(tenant: str, handle: str, *, kind: str, label: str, origin: dict | None = None) -> Handle:
    """Record metadata for a reserved handle whose file now exists."""
    matches = [p for p in tenant_dir(tenant).glob(f"{handle}.*") if p.suffix != ".json"]
    if not matches:
        raise ToolError(f"Reserved handle {handle!r} has no file to register.")
    path = matches[0]
    meta = {
        "handle": handle, "kind": kind, "label": label,
        "media_type": _MEDIA.get(path.suffix, "application/octet-stream"),
        "size_bytes": path.stat().st_size,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "origin": origin or {},
    }
    (path.parent / f"{handle}.json").write_text(json.dumps(meta))
    return {k: meta[k] for k in ("handle", "kind", "label", "media_type", "size_bytes")}


def owned(tenant: str, path: str | Path) -> str | None:
    """The reserved handle id if ``path`` is a handle path of ``tenant``, else None."""
    p = Path(path)
    if _HANDLE_RE.match(p.stem) and p.resolve().parent == tenant_dir(tenant).resolve():
        return p.stem
    return None


def mint(tenant: str, path: str | Path, *, kind: str, label: str, origin: dict | None = None) -> Handle:
    """Register a file the caller did not place under a handle path: copies it in.

    Use ``reserve`` + ``register`` when you control where the file is written.
    """
    src = Path(path)
    if not src.is_file():
        raise ToolError(f"Cannot mint a handle for a missing file: {src}")
    existing = owned(tenant, src)
    if existing:
        return register(tenant, existing, kind=kind, label=label, origin=origin)
    handle, dest = reserve(tenant, src.suffix)
    shutil.copy2(src, dest)
    return register(tenant, handle, kind=kind, label=label, origin=origin)


def resolve(tenant: str, handle: str) -> Path:
    """Path of ``handle`` for ``tenant``; a ToolError if it is not theirs."""
    if not is_handle(handle):
        raise ToolError(f"{handle!r} is not an output handle (expected out_<12 hex>).")
    matches = [p for p in tenant_dir(tenant).glob(f"{handle}.*") if p.suffix != ".json"]
    if not matches:
        raise ToolError(f"Unknown handle {handle!r}. Handles are per tenant and live under the "
                        "output root; list_outputs shows yours.")
    return matches[0]


def describe(tenant: str, handle: str) -> dict[str, Any]:
    path = resolve(tenant, handle)
    meta_path = path.with_suffix(".json")
    meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
    meta.setdefault("handle", handle)
    meta["path"] = str(path)
    return meta


def listing(tenant: str) -> list[dict[str, Any]]:
    out = []
    for meta_path in sorted(tenant_dir(tenant).glob("out_*.json")):
        meta = json.loads(meta_path.read_text())
        out.append({k: meta.get(k) for k in ("handle", "kind", "label", "media_type",
                                              "size_bytes", "created_at")})
    return out


def path_or_handle(tenant: str, value: str) -> str:
    """Accept a handle or a path wherever a primitive wants a path."""
    return str(resolve(tenant, value)) if is_handle(value) else value
