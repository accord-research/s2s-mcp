"""Per-tenant cache of recipe results, keyed by the recipe's arguments.

A hosted demo (and a real agency) asks for the same outlook repeatedly, and
the widget's method switch re-runs the recipe with one argument changed.
acmadDL caches the downloads, but the pipeline itself still costs seconds to
a minute; against ChatGPT's roughly one-minute tool timeout that matters.
Entries are JSON sidecars under ``<tenant_dir>/cache/`` and are only served
while every handle they name still resolves.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from . import handles
from .tenancy import tenant_dir


# Bump when the shape or wording of a cached result changes, so stale entries miss.
CACHE_VERSION = "2026-10-12.4"


def enabled() -> bool:
    return os.environ.get("S2S_RECIPE_CACHE", "1") != "0"


def key(args: dict[str, Any]) -> str:
    canonical = json.dumps({"_v": CACHE_VERSION, **args}, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _path(tenant: str, k: str) -> Path:
    d = tenant_dir(tenant) / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{k}.json"


def get(tenant: str, args: dict[str, Any]) -> dict[str, Any] | None:
    if not enabled():
        return None
    path = _path(tenant, key(args))
    if not path.is_file():
        return None
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        path.unlink(missing_ok=True)
        return None
    for value in result.values():
        if handles.is_handle(value):
            try:
                handles.resolve(tenant, value)
            except ToolError:
                path.unlink(missing_ok=True)
                return None
    return result


def put(tenant: str, args: dict[str, Any], result: dict[str, Any]) -> None:
    if not enabled():
        return
    _path(tenant, key(args)).write_text(json.dumps(result), encoding="utf-8")
