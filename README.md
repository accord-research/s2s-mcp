# s2s-mcp

A hosted [Model Context Protocol](https://modelcontextprotocol.io) server that composes [acmadDL](https://github.com/ACMAD-Niamey/acmadDL) (climate data access) and [africas2s](https://github.com/ACMAD-Niamey/africas2s) (seasonal forecast downscaling, calibration, and verification) into a service a regional centre can run for national hydromet agencies.

The agencies' agents, possibly small and cheap, need no compute, no provider credentials, and no more than a handful of tools. Data, the CDS account, the download cache, and every intermediate file stay on the host. The agent receives handles and skill tables.

## What it adds on top of the two libraries

acmadDL and africas2s each ship a thin MCP server for local use (`acmaddl-mcp`, `africas2s-mcp`). This package is the deployment:

- **Recipes.** `seasonal_outlook` runs ACMAD's standard workflow in one call: fetch the forecast models and the observations, run the multi-model seasonal pipeline with leave-year-out cross-validation, verify it, and draw the tercile-probability map.
- **A pipeline runner.** `run_pipeline` chains any of the underlying operations (`acmaddl.fetch`, `africas2s.downscale`, `africas2s.skill`, ...) on the server, with `$step.field` references between steps, and returns only the final result. This is the "code execution with MCP" idea, hosted server-side for agents that cannot run code, without running user code: the step vocabulary is exactly the primitives' schemas.
- **Progressive disclosure.** The default tool surface is eight tools. The twenty-odd primitives are reachable by name through `run_pipeline` and described on demand by `list_operations` / `describe_operation`. `--advanced` also registers each primitive as a tool.
- **Handles instead of paths.** Every output is a server-minted `out_<hex>` handle, scoped to the calling tenant. `get_output` returns a PNG inline as an image and a data file as a download link.
- **Tenancy plumbing.** Per-tenant output directories, tenant resolution from a proxy-set header, and a download route on the HTTP app. Authentication itself belongs to the proxy or to a future OAuth `token_verifier`.

It holds no data-access or forecasting logic of its own. Every step is a documented library call.

## Running it

```bash
uv sync
uv run s2s-mcp                                     # stdio, for a local client
uv run s2s-mcp --transport streamable-http --stateless --port 8080
uv run s2s-mcp --advanced                          # also expose every primitive as a tool
```

Environment:

| Variable | Meaning | Default |
|---|---|---|
| `S2S_OUTPUT_ROOT` | Where per-tenant outputs live | `~/.s2s-mcp/outputs` |
| `S2S_TENANT_MODE` | `single` (one tenant) or `header` (trust `X-S2S-Tenant` from the auth proxy) | `single` |
| `S2S_PUBLIC_BASE_URL` | Public base URL, used to build download links for data outputs | unset |
| `ACMADDL_CACHE_DIR` | acmadDL's shared download cache | `~/.nuthatch/caches` |
| `CDSAPI_URL`, `CDSAPI_KEY` | Copernicus credentials, held by the host | |

A container build and a compose file are under `deploy/`. Mount the cache and output volumes; put TLS and authentication in front.

## Using it from an agent

Register the server and ask for an outlook:

```
seasonal_outlook(init="2025-02", target="MAM", region=[-12, 6, 28, 42],
                 models=["c3s/ecmwf-monthly", "nmme/ccsm4"])
-> {tercile_map: "out_…", tercile_forecast: "out_…", cv_hindcast: "out_…",
    deterministic: "out_…", skill: {rpss: 0.12, …}, metadata: {…}}
get_output("out_…")   # the map, inline
```

For a custom chain:

```
run_pipeline(steps=[
  {"id": "hind", "op": "acmaddl.fetch", "args": {"product": "nmme/ccsm4", "variable": "precip",
    "init": "2025-02", "target": "MAM", "region": [-12, 6, 28, 42], "hindcast": [1993, 2016], "year_index": true}},
  {"id": "obs",  "op": "acmaddl.fetch", "args": {"product": "obs/chirps-v3-monthly", "variable": "precip",
    "region": [-12, 6, 28, 42], "hindcast": [1993, 2016], "target": "MAM", "seasonal": true}},
  {"id": "fc",   "op": "africas2s.downscale", "args": {"predictor_hindcast_path": "$hind.handle",
    "obs_path": "$obs.handle", "method": "bcsd", "output_type": "tercile"}},
  {"id": "map",  "op": "africas2s.plot_terciles", "args": {"probs_path": "$fc.handle"}}
], keep=["fc"])
```

The agent sees the map handle and a brief summary of `fc`. The hindcast and observation files never enter its context.

## Protocol

Implements MCP revision 2026-07-28 through the `mcp` 2.x SDK: stateless core, `server/discover`, cache freshness hints on list results, Streamable HTTP as the only network transport. Long-running calls will move to the Tasks extension once the Python SDK ships server-side support.

## Development

```bash
uv sync --extra dev
uv run pytest -q
```

Tests use synthetic data and stub acmadDL's network verbs; the africas2s pipeline runs for real. Until the `[mcp]` extras are released on PyPI, `pyproject.toml` resolves the two libraries from their `main` branches; delete the `[tool.uv.sources]` block after release so the PyPI pins take over.
