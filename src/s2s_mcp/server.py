"""The hosted s2s MCP server.

Default surface (what a small agent sees): a recipe, a pipeline runner, and
the few lookups needed to use them. The twenty-odd primitives from acmaddl
and africas2s stay out of the schema; they are reachable by name through
``run_pipeline`` and discoverable through ``list_operations`` /
``describe_operation`` (progressive disclosure). ``--advanced`` also
registers every primitive as a tool for power users.
"""

from __future__ import annotations

import argparse
import base64
import importlib.metadata
import inspect
import os
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.caching import CacheHint
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ImageContent, TextContent, ToolAnnotations
from pydantic import Field

from acmaddl.mcp import server as _acmaddl
from africas2s.mcp import server as _africas2s

from . import grid, handles, jobs, pipeline, recipes
from .apps import APP_ONLY_META, IMAGE_TOOL_META, IMAGE_URI, OUTLOOK_TOOL_META, OUTLOOK_URI, apps
from .tenancy import tenant_dir, tenant_from_headers, tenant_mode

_SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "s2s"
if not _SKILL_DIR.is_dir():  # installed wheel: the skill is force-included next to the package
    _SKILL_DIR = Path(__file__).resolve().parent / "skill"

_INSTRUCTIONS = """\
s2s composes acmadDL (climate data access) and africas2s (seasonal forecast
downscaling, calibration, and verification) into one hosted service. Data,
credentials, and compute stay on this server; you receive handles
(out_xxxxxxxxxxxx) for every file produced, and skill tables inline.

Start with seasonal_outlook: one call fetches the forecast models and the
observations, runs the multi-model seasonal pipeline with honest
cross-validation, and returns a tercile-probability map, the forecast files,
and the skill scores. Use get_output to see the map or download a file.

For anything the recipe does not cover, run_pipeline chains the underlying
operations (acmaddl.fetch, africas2s.downscale, africas2s.skill, ...) in one
call; list_operations and describe_operation show what is available. Only
the final step's result comes back to you.

Product ids for models and observations come from list_products.
"""


def _dist_version() -> str:
    try:
        return importlib.metadata.version("s2s-mcp")
    except importlib.metadata.PackageNotFoundError:
        return ""


_DAY_MS = 24 * 60 * 60 * 1000
_CACHE_HINTS = {
    "tools/list": CacheHint(ttl_ms=_DAY_MS, scope="public"),
    "prompts/list": CacheHint(ttl_ms=_DAY_MS, scope="public"),
    "resources/list": CacheHint(ttl_ms=_DAY_MS, scope="public"),
    "resources/templates/list": CacheHint(ttl_ms=_DAY_MS, scope="public"),
    "resources/read": CacheHint(ttl_ms=60 * 60 * 1000, scope="public"),
}

mcp = MCPServer(
    name="s2s",
    title="S2S seasonal forecasting (hosted)",
    instructions=_INSTRUCTIONS,
    version=_dist_version(),
    website_url="https://github.com/accord-research/s2s-mcp",
    cache_hints=_CACHE_HINTS,
    extensions=[apps],
)


def tool(*, read_only: bool = False, idempotent: bool = True, open_world: bool = False,
         destructive: bool = False, meta: dict | None = None, **kwargs):
    annotations = ToolAnnotations(read_only_hint=read_only, destructive_hint=destructive,
                                  idempotent_hint=idempotent, open_world_hint=open_world)

    def decorate(fn):
        description = kwargs.pop("description", None) or inspect.cleandoc(fn.__doc__ or "")
        return mcp.tool(annotations=annotations, description=description, meta=meta, **kwargs)(fn)

    return decorate


def _tenant(ctx) -> str:
    headers = None
    if ctx is not None:
        try:
            headers = ctx.headers
        except Exception:  # noqa: BLE001  (no request context: stdio or a direct call)
            headers = None
    return tenant_from_headers(headers)


# --------------------------------------------------------------------------
# recipe
# --------------------------------------------------------------------------

RegionArg = Annotated[list[float] | str, Field(
    description="[lat_s, lat_n, lon_w, lon_e] bounding box in degrees, e.g. [-12, 6, 28, 42] "
                "for East Africa, or a shapefile path on the server.")]


# Every NMME product that serves precipitation. Pooled tercile probabilities need
# spread across models, so a single-model default produced 0/1 maps.
# How soon a widget or agent should ask again after a "running" answer.
POLL_AFTER_SECONDS = 8
# Default in-call wait before answering "running"; hosts budget about a minute per call.
DEFAULT_WAIT_SECONDS = int(os.environ.get("S2S_OUTLOOK_WAIT_SECONDS", "45"))

NMME_PRECIP_MODELS = ["nmme/ccsm4", "nmme/cfsv2", "nmme/geoss2s", "nmme/cansipsic4", "nmme/spear", "nmme/cesm1"]


@tool(open_world=True, idempotent=False, meta={"ui": {"resourceUri": OUTLOOK_URI}, **OUTLOOK_TOOL_META})
def seasonal_outlook(
    init: Annotated[str, Field(description='Forecast issuance month, "YYYY-MM", e.g. "2025-02".')],
    target: Annotated[str, Field(description='Target season as month initials, e.g. "MAM", "OND", "JJAS".')],
    region: RegionArg,
    models: Annotated[list[str], Field(
        description='Forecast model product ids from list_products, e.g. ["c3s/ecmwf-monthly", "nmme/ccsm4"]. '
                    'Defaults to the six NMME precipitation models; keep several unless the user asks for one.',
        min_length=1)] = NMME_PRECIP_MODELS,
    predictand: Annotated[str, Field(
        description='Observation product used as the predictand, e.g. "obs/chirps-v3-monthly" (rainfall) '
                    'or "obs/era5" (temperature).')] = "obs/chirps-v3-monthly",
    variable: Annotated[Literal["precip", "temp"], Field(description="Variable to forecast.")] = "precip",
    hindcast: Annotated[list[int], Field(
        description="[start_year, end_year] training window shared by models and observations.",
        min_length=2, max_length=2)] = [1993, 2016],
    method: Annotated[Literal["cca", "bcsd", "qm", "dqm", "delta", "climatology", "rank-analog"], Field(
        description='Downscaling method. "cca" is the PyCPT-style default; "climatology" is the no-skill baseline.')] = "cca",
    cv_window: Annotated[int, Field(
        description="Years left out per cross-validation fold (5 matches PyCPT).", ge=1, le=11)] = 5,
    title: Annotated[str | None, Field(description="Map title; default is built from the request.")] = None,
    wait_seconds: Annotated[int, Field(
        description="How long to wait for a fresh run before answering with status 'running' (0 only checks). "
                    "Cached and quick runs return at once regardless.", ge=0, le=55)] = DEFAULT_WAIT_SECONDS,
    ctx: Context = None,
) -> dict[str, Any]:
    """Produce a complete seasonal outlook in one call: fetch the models and
    observations, run the multi-model seasonal pipeline with leave-year-out
    cross-validation, verify it, and draw the tercile-probability map.

    Repeats of the same request return instantly. A first run for a region
    fetches the models and can take minutes, longer than a tool call may run:
    the run then continues on the server and the call answers {status:
    "running", request, elapsed_seconds, poll_after_seconds, message}. Tell
    the user the outlook is computing and that the card fills in by itself;
    to collect it yourself, call again with the same arguments after
    poll_after_seconds. Complete results carry status "done".

    Hosts that support MCP Apps render the result as an interactive map. After
    the card renders, give a two-to-three sentence high-level reading: where
    the signal is, how much to trust it given the skill, and the main caveat.
    Use the highlights; do not repeat numbers the card already shows.

    Returns: {tercile_map (handle, PNG), tercile_forecast (handle, NetCDF
    (tercile, lat, lon)), cv_hindcast (handle), deterministic (handle),
    skill: {rpss, ...}, metadata, models, bounds [lat_s, lat_n, lon_w, lon_e],
    request (the arguments used), highlights {region_shares, countries,
    strongest, skill_verdict, region_label, summary}, summary, palette (how the
    map is coloured: describe colours from this, never from memory)}.
    Show the map with get_output(tercile_map) when no widget is rendered.
    Example: seasonal_outlook(init="2025-02", target="MAM", region=[-12, 6, 28, 42],
    models=["c3s/ecmwf-monthly", "nmme/ccsm4"])
    """
    for product in list(models) + [predictand]:
        _acmaddl._require_product(product)
    tenant = _tenant(ctx)
    kwargs: dict[str, Any] = dict(models=list(models), predictand=predictand, variable=variable, init=init,
                                  target=target, region=region, hindcast=list(hindcast), method=method,
                                  cv_window=cv_window, title=title)
    status, payload = jobs.registry.run(
        tenant, recipes.outlook_key(**kwargs),
        lambda: recipes.seasonal_outlook(tenant=tenant, **kwargs), wait=wait_seconds)
    if status == "running":
        return {"status": "running", "request": recipes.normalised_request(**kwargs),
                "elapsed_seconds": payload["elapsed_seconds"], "poll_after_seconds": POLL_AFTER_SECONDS,
                "message": "The outlook is computing on the server; a first run for a region fetches the "
                           "models and can take a few minutes. The card fills in by itself; to collect the "
                           "result otherwise, call seasonal_outlook again with the same arguments."}
    return {"status": "done", **payload}


# --------------------------------------------------------------------------
# pipeline + discovery
# --------------------------------------------------------------------------

class Step(dict):
    pass


@tool(open_world=True, idempotent=False)
async def run_pipeline(
    steps: Annotated[list[dict[str, Any]], Field(
        description='Ordered steps, each {"id": "name", "op": "acmaddl.fetch", "args": {...}}. '
                    'Arguments may reference earlier steps as "$id.field" (e.g. "$hind.handle") '
                    'or pass output handles directly wherever a path is expected.',
        min_length=1)],
    keep: Annotated[list[str] | None, Field(
        description="Step ids whose results to return in addition to the final step's.")] = None,
    verbosity: Annotated[Literal["brief", "full"], Field(
        description='"brief" trims dataset summaries to dims and variable units; "full" returns everything.')] = "brief",
    ctx: Context = None,
) -> dict[str, Any]:
    """Run a chain of operations on the server and return only the final result.

    Use this when seasonal_outlook does not fit: a different observation
    product, a calibration instead of downscaling, an ensemble of your own
    forecasts, zonal statistics per district. Each step is one operation from
    list_operations with the arguments describe_operation documents. Files
    never leave the server; every output becomes a handle.

    Returns: {run_id, steps: [{id, op, status, handle}], result, kept?}.
    Example: run_pipeline(steps=[
      {"id": "hind", "op": "acmaddl.fetch", "args": {"product": "nmme/ccsm4", "variable": "precip",
        "init": "2025-02", "target": "MAM", "region": [-12, 6, 28, 42], "hindcast": [1993, 2016],
        "year_index": true}},
      {"id": "obs", "op": "acmaddl.fetch", "args": {"product": "obs/chirps-v3-monthly", "variable": "precip",
        "region": [-12, 6, 28, 42], "hindcast": [1993, 2016], "target": "MAM", "seasonal": true}},
      {"id": "fc", "op": "africas2s.downscale", "args": {"predictor_hindcast_path": "$hind.handle",
        "obs_path": "$obs.handle", "method": "bcsd", "output_type": "tercile"}},
      {"id": "map", "op": "africas2s.plot_terciles", "args": {"probs_path": "$fc.handle"}}
    ], keep=["fc"])
    """
    return await pipeline.run_pipeline(list(steps), _tenant(ctx), keep=keep, verbosity=verbosity)


@tool(read_only=True)
def list_operations() -> list[dict[str, Any]]:
    """Operations run_pipeline can chain, one line each.

    Returns: [{op, summary, accepts_destination}], e.g. op "acmaddl.fetch",
    "africas2s.downscale". Use describe_operation for a full schema.
    """
    return [{"op": op.name, "summary": op.summary, "accepts_destination": op.accepts_destination}
            for op in pipeline.OPERATIONS.values()]


@tool(read_only=True)
def describe_operation(
    op: Annotated[str, Field(description='Operation name from list_operations, e.g. "africas2s.skill".')],
) -> dict[str, Any]:
    """Full description, input schema, and output schema of one operation.

    Returns: {op, description, input_schema, output_schema}.
    """
    operation = pipeline.operation(op)
    return {"op": operation.name, "description": operation.description,
            "input_schema": operation.input_schema, "output_schema": operation.output_schema}


# --------------------------------------------------------------------------
# lookups and outputs
# --------------------------------------------------------------------------

@tool(read_only=True)
def list_products(
    include_deprecated: Annotated[bool, Field(description="Also list deprecated products.")] = False,
) -> list[dict[str, Any]]:
    """Forecast and observation products available on this server, with the
    variables each serves. Ids like "nmme/ccsm4" or "c3s/ecmwf-monthly" are
    models; "obs/..." are observations usable as the predictand.

    Returns: [{product, adapter, variables, deprecated, notes}].
    """
    return _acmaddl.list_products(include_deprecated=include_deprecated)


@tool(read_only=True)
def describe_dataset(
    handle: Annotated[str, Field(description="An output handle (out_...) from any tool here.")],
    ctx: Context = None,
) -> dict[str, Any]:
    """Dims, coordinate ranges, variables, and units of a NetCDF output.

    Returns: {handle, dims, coords, variables, attrs, size_bytes}.
    """
    tenant = _tenant(ctx)
    path = handles.resolve(tenant, handle)
    summary = _acmaddl.describe_dataset(str(path))
    summary.pop("path", None)
    return {"handle": handle, **summary}


@tool(read_only=True)
def list_outputs(ctx: Context = None) -> list[dict[str, Any]]:
    """Every output you have produced on this server, newest last.

    Returns: [{handle, kind, label, media_type, size_bytes, created_at}].
    """
    return handles.listing(_tenant(ctx))


def _download_url(tenant: str, handle: str) -> str | None:
    base = os.environ.get("S2S_PUBLIC_BASE_URL")
    if not base:
        return None
    return f"{base.rstrip('/')}/outputs/{tenant}/{handle}"


@tool(read_only=True)
def get_output(
    handle: Annotated[str, Field(description="An output handle (out_...).")],
    ctx: Context = None,
) -> list[ImageContent | TextContent]:
    """Fetch an output: a PNG map is returned inline as an image; a data file
    is returned as a download link (when the server has a public URL) plus
    its description.

    Returns: an image block for PNGs, otherwise a JSON text block
    {handle, media_type, size_bytes, download_url, path}.
    """
    import json

    tenant = _tenant(ctx)
    path = handles.resolve(tenant, handle)
    meta = handles.describe(tenant, handle)
    if meta.get("media_type") == "image/png":
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return [ImageContent(type="image", data=data, mime_type="image/png"),
                TextContent(type="text", text=json.dumps({"handle": handle, "label": meta.get("label")}))]
    url = _download_url(tenant, handle)
    info = {"handle": handle, "label": meta.get("label"), "media_type": meta.get("media_type"),
            "size_bytes": meta.get("size_bytes"), "download_url": url}
    if url is None:
        info["path"] = str(path)
        info["note"] = "No public URL configured (S2S_PUBLIC_BASE_URL); the path is on the server host."
    table = _small_table(path)
    if table is not None:
        info["values"] = table
        info["values_note"] = (f"Inline because the dataset is small ({len(table)} rows). "
                               "Larger datasets return only the download link.")
    return [TextContent(type="text", text=json.dumps(info))]


_SMALL_TABLE_MAX_CELLS = 2000


def _small_table(path) -> list[dict[str, Any]] | None:
    """Rows of a small NetCDF (zonal statistics, skill tables) so a thin agent can
    read the numbers without downloading the file. None for anything larger than
    ``_SMALL_TABLE_MAX_CELLS`` values, non-NetCDF files, or unreadable ones."""
    if str(path).rsplit(".", 1)[-1].lower() not in {"nc", "nc4", "netcdf"}:
        return None
    try:
        import math
        import numpy as np
        import xarray as xr
        with xr.open_dataset(path) as ds:
            cells = sum(int(np.prod(v.shape)) for v in ds.data_vars.values())
            if cells == 0 or cells > _SMALL_TABLE_MAX_CELLS:
                return None
            df = ds.to_dataframe().reset_index()
        rows: list[dict[str, Any]] = []
        for rec in df.to_dict(orient="records"):
            clean = {}
            for k, v in rec.items():
                if isinstance(v, (np.floating, float)):
                    clean[k] = None if math.isnan(v) else round(float(v), 4)
                elif isinstance(v, np.integer):
                    clean[k] = int(v)
                else:
                    clean[k] = v if isinstance(v, (str, int, bool)) or v is None else str(v)
            rows.append(clean)
        return rows
    except Exception:  # noqa: BLE001 - preview is best-effort
        return None


@tool(read_only=True, meta={"ui": {"resourceUri": IMAGE_URI}, **IMAGE_TOOL_META})
def view_output(
    handle: Annotated[str, Field(description="Handle (out_...) of a PNG output, e.g. the map from africas2s.plot_field or plot_terciles.")],
    title: Annotated[str | None, Field(description="Caption for the card. Default: the output's label.")] = None,
    ctx: Context = None,
) -> dict[str, Any]:
    """Show a PNG output as an interactive card. In hosts with MCP Apps the map
    renders inline with Expand and Open actions; elsewhere the text result carries
    the download link. Call this after any plot step so the user sees the map
    rather than a link. For data files use get_output.

    Returns: {handle, title, label, media_type, size_bytes, download_url}.
    """
    tenant = _tenant(ctx)
    handles.resolve(tenant, handle)
    meta = handles.describe(tenant, handle)
    if meta.get("media_type") != "image/png":
        raise ToolError(f"{handle} is {meta.get('media_type') or 'not an image'}; view_output shows PNG maps. "
                        "Use get_output for data files.")
    return {"handle": handle, "title": title or meta.get("label"), "label": meta.get("label"),
            "media_type": "image/png", "size_bytes": meta.get("size_bytes"),
            "download_url": _download_url(tenant, handle)}


@tool(read_only=True, meta=APP_ONLY_META)
def output_image(
    handle: Annotated[str, Field(description="Handle of a PNG output.")],
    ctx: Context = None,
) -> dict[str, Any]:
    """The bytes of a PNG output for the map card (app-only; agents use get_output).

    Returns: {handle, media_type, data_base64}.
    """
    tenant = _tenant(ctx)
    path = handles.resolve(tenant, handle)
    meta = handles.describe(tenant, handle)
    if meta.get("media_type") != "image/png":
        raise ToolError(f"{handle} is not a PNG output.")
    return {"handle": handle, "media_type": "image/png",
            "data_base64": base64.b64encode(path.read_bytes()).decode("ascii")}


@tool(read_only=True, meta=APP_ONLY_META)
def outlook_grid(
    handle: Annotated[str, Field(description="Handle of a (tercile, lat, lon) probability forecast, "
                                             "e.g. seasonal_outlook's tercile_forecast.")],
    max_cells: Annotated[int, Field(description="Upper bound on grid cells after block-averaging.",
                                    ge=1000, le=60000)] = 20000,
    ctx: Context = None,
) -> dict[str, Any]:
    """Probability grid for the outlook widget (used by the widget, not by agents):
    the forecast block-averaged to at most max_cells cells, plus the palette.

    Returns: {handle, bounds, lat, lon, factor, below, normal, above, style}.
    Example: outlook_grid(handle="out_0123456789ab")
    """
    import xarray as xr

    tenant = _tenant(ctx)
    path = handles.resolve(tenant, handle)
    try:
        with xr.open_dataset(path) as ds:
            names = list(ds.data_vars)
            var = "probability" if "probability" in names else (names[0] if len(names) == 1 else None)
            if var is None:
                raise ToolError(f"{handle!r} holds {len(names)} data variables ({', '.join(names[:6])}); "
                                "outlook_grid needs a single (tercile, lat, lon) probability field.")
            payload = grid.coarsen_probs(ds[var].load(), max_cells=max_cells)
    except ToolError:
        raise
    except ValueError as exc:
        raise ToolError(f"{handle!r}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        # Only the exception type: OS-level messages can carry the host path.
        raise ToolError(f"Could not read {handle!r} as a probability field ({type(exc).__name__}).") from exc
    return {"handle": handle, **payload, "style": grid.tercile_style()}


# --------------------------------------------------------------------------
# resources
# --------------------------------------------------------------------------

@mcp.resource("s2s://skill", mime_type="text/markdown",
              description="How to use this server: recipes, pipelines, handles, conventions.")
def skill_resource() -> str:
    path = _SKILL_DIR / "SKILL.md"
    return path.read_text(encoding="utf-8") if path.is_file() else "SKILL.md not found in this install."


# --------------------------------------------------------------------------
# advanced mode: every primitive as a tool (single-tenant only)
# --------------------------------------------------------------------------

def register_primitives() -> list[str]:
    """Expose acmaddl.* and africas2s.* primitives as tools named with a prefix.

    Handles in arguments are resolved and paths in results are handleized
    against the single default tenant, so this is only offered when the
    server runs in single-tenant mode.
    """
    if tenant_mode() != "single":
        raise RuntimeError("--advanced requires S2S_TENANT_MODE=single.")
    names = []
    for op in pipeline.OPERATIONS.values():
        tool_name = op.name.replace(".", "_")

        async def _call(op=op, **kwargs):
            args = pipeline.resolve_args(kwargs, {}, "default")
            return await pipeline.run_step(op, args, "default", run_id="direct", step_id=op.tool)

        module = _acmaddl if op.name.startswith("acmaddl.") else _africas2s
        original = getattr(module, op.tool)
        # Same schema as the primitive. The libraries use postponed annotations,
        # so resolve them here (eval_str) or pydantic cannot see their aliases.
        signature = inspect.signature(original, eval_str=True)
        _call.__signature__ = signature
        _call.__annotations__ = {name: p.annotation for name, p in signature.parameters.items()
                                 if p.annotation is not inspect.Parameter.empty}
        _call.__name__ = tool_name
        _call.__doc__ = original.__doc__
        mcp.tool(name=tool_name, description=inspect.cleandoc(original.__doc__ or ""))(_call)
        names.append(tool_name)
    return names


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def build_http_app(*, stateless: bool, json_response: bool, host: str):
    """Starlette app: the MCP endpoint plus a download route for data outputs."""
    from starlette.requests import Request
    from starlette.responses import FileResponse, JSONResponse
    from starlette.routing import Route

    app = mcp.streamable_http_app(stateless_http=stateless, json_response=json_response, host=host)

    async def download(request: Request):
        tenant = request.path_params["tenant"]
        handle = request.path_params["handle"]
        try:
            caller = tenant_from_headers(request.headers)
        except ToolError as exc:
            return JSONResponse({"error": str(exc)}, status_code=401)
        if caller != tenant:
            return JSONResponse({"error": "handle belongs to another tenant"}, status_code=403)
        try:
            path = handles.resolve(tenant, handle)
        except ToolError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return FileResponse(path, filename=path.name)

    app.router.routes.append(Route("/outputs/{tenant}/{handle}", download, methods=["GET"]))
    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="s2s-mcp", description="Run the hosted s2s MCP server.")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default=os.environ.get("S2S_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("S2S_PORT", "8080")))
    parser.add_argument("--stateless", action="store_true",
                        help="Streamable HTTP without server-side session state.")
    parser.add_argument("--json-response", action="store_true")
    parser.add_argument("--advanced", action="store_true",
                        help="Also expose every acmaddl/africas2s primitive as a tool (single tenant only).")
    args = parser.parse_args(argv)
    if args.advanced:
        register_primitives()
    if args.transport == "stdio":
        mcp.run("stdio")
        return
    import uvicorn

    app = build_http_app(stateless=args.stateless, json_response=args.json_response, host=args.host)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
