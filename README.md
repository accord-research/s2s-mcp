# s2s-mcp

A hosted [Model Context Protocol](https://modelcontextprotocol.io) server that composes [acmadDL](https://github.com/ACMAD-Niamey/acmadDL) (climate data access) and [africas2s](https://github.com/ACMAD-Niamey/africas2s) (seasonal forecast downscaling, calibration, and verification) into a service a regional centre can run for national hydromet agencies.

The agencies' agents, possibly small and cheap, need no compute, no provider credentials, and no more than a handful of tools. Data, the CDS account, the download cache, and every intermediate file stay on the host. The agent receives handles and skill tables.

## What it adds on top of the two libraries

acmadDL and africas2s each ship a thin MCP server for local use (`acmaddl-mcp`, `africas2s-mcp`). This package is the deployment:

- **Recipes.** `seasonal_outlook` runs ACMAD's standard workflow in one call: fetch the forecast models and the observations, run the multi-model seasonal pipeline with leave-year-out cross-validation, verify it, and draw the tercile-probability map.
- **A pipeline runner.** `run_pipeline` chains any of the underlying operations (`acmaddl.fetch`, `africas2s.downscale`, `africas2s.skill`, ...) on the server, with `$step.field` references between steps, and returns only the final result. This is the "code execution with MCP" idea, hosted server-side for agents that cannot run code, without running user code: the step vocabulary is exactly the primitives' schemas.
- **Progressive disclosure.** The default tool surface is nine tools (plus `outlook_grid` and `output_image`, which only the widgets call). The twenty-odd primitives are reachable by name through `run_pipeline` and described on demand by `list_operations` / `describe_operation`. `--advanced` also registers each primitive as a tool.
- **Handles instead of paths.** Every output is a server-minted `out_<hex>` handle, scoped to the calling tenant. `get_output` returns a PNG inline as an image and a data file as a download link; a small data file (at most 2,000 values, such as zonal statistics or a skill table) also carries its rows inline, so a thin agent can quote the numbers without a round trip. `view_output` shows a PNG as a card in hosts with MCP Apps.
- **Tenancy plumbing.** Per-tenant output directories, tenant resolution from a proxy-set header, and a download route on the HTTP app. Authentication itself belongs to the proxy or to a future OAuth `token_verifier`.
- **An interactive outlook (MCP Apps).** In hosts that implement [MCP Apps](https://modelcontextprotocol.io/extensions/apps/overview) (ChatGPT, Claude, Claude Desktop, VS Code), `seasonal_outlook` renders as a live tercile map with hover probabilities, a method switch (CCA / BCSD / climatology re-run on cached data), computed highlights, an Explain action that asks the model for a short reading, and a fullscreen view. Text-only hosts see the same result as JSON and can still fetch the PNG with `get_output`. Any PNG a pipeline step draws (`africas2s.plot_field`, `africas2s.plot_terciles`) renders the same way through `view_output`: a card with the map, an Expand action and a link to the file.

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
| `ECMWF_DATASTORES_URL`, `ECMWF_DATASTORES_KEY` | ECMWF Data Stores key for `weather-skills/ecmwf-s2s` (a Copernicus CDS key is refused) | |
| `EARTHDATA_TOKEN` | NASA Earthdata token (bare token, not username/password) for `weather-skills/imerg-daily*` and `weather-skills/smap-daily` | |
| `S2S_OUTLOOK_WAIT_SECONDS` | Default in-call wait of `seasonal_outlook` before it answers `status: "running"` (per-call `wait_seconds` overrides it, max 55) | `45` |
| `S2S_RECIPE_CACHE` | `0` disables the per-tenant cache of `seasonal_outlook` results (keyed by arguments) | `1` |

A container build and a compose file are under `deploy/`. Mount the cache and output volumes; put TLS and authentication in front.

## Using it from an agent

Register the server and ask for an outlook:

```
seasonal_outlook(init="2025-02", target="MAM", region=[-12, 6, 28, 42],
                 models=["c3s/ecmwf-monthly", "nmme/ccsm4"])
-> {tercile_map: "out_…", tercile_forecast: "out_…", cv_hindcast: "out_…",
    deterministic: "out_…", skill: {rpss: 0.12, …}, metadata: {…},
    bounds: [-12.1, 6.1, 27.9, 42.1], request: {…the arguments used…},
    highlights: {region_shares: {…}, countries: […], strongest: {…}, skill_verdict: "modest",
                 region_label: "Ethiopia, Kenya, Tanzania", summary: "Above-normal dominant over 41% …"},
    summary: "Above-normal dominant over 41% …",
    palette: "Map colours: greens mark above-normal (wetter) cells, yellows through reds mark below-normal …"}
get_output("out_…")   # the map, inline
view_output("out_…")  # the map as a card in ChatGPT or Claude
```

The same request twice is served from the per-tenant result cache. `highlights` are computed on the server (area-weighted shares of the dominant tercile for the region and per country, the strongest wet and dry signals, a one-word skill verdict) so a model's description of the outlook is grounded in numbers rather than in the picture.

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

The agent sees the map handle and a brief summary of `fc`; `view_output` on the map handle shows it as a card. `get_output` on a data handle gives a download link, and includes the rows when the file is small (at most 2,000 values, as zonal statistics and skill tables are). The hindcast and observation files never enter its context.

## Interactive outlook in ChatGPT or Claude

Two widgets ship as self-contained HTML files built from `web/` and checked in under `src/s2s_mcp/web/`, each served as an MCP App resource: `outlook.html` (`ui://s2s/outlook`) for `seasonal_outlook`, and `image.html` (`ui://s2s/image`) for `view_output`, which shows any PNG output (a `plot_field` or `plot_terciles` map) as a card with Expand and Open PNG actions. A tool points at its widget through `_meta.ui.resourceUri` plus ChatGPT's `openai/outputTemplate` alias. The widgets fetch what they draw through app-only tools the model does not see: `outlook_grid` hands the outlook a coarsened probability grid plus the ACMAD palette, and `output_image` hands the map card the PNG bytes, so the model only receives the handle, caption, size and download link. The bundles have no external script, style, or connect origins, so the server needs no Node at runtime. Rebuild after changing `web/`:

```bash
cd web && npm install && npm run build      # writes src/s2s_mcp/web/outlook.html, image.html and africa-countries.geojson
```

To use it from a laptop:

1. `uv run s2s-mcp --transport streamable-http --stateless --port 8080` (with `CDSAPI_URL`/`CDSAPI_KEY` set if a model needs CDS).
2. `npx cloudflared tunnel --url http://localhost:8080 --http-host-header localhost:8080` and copy the `https://….trycloudflare.com` URL (it changes on every restart). The `--http-host-header` flag matters: the MCP SDK's DNS-rebinding guard only accepts `localhost`/`127.0.0.1` Host headers, and without the rewrite every request through the tunnel fails with "Invalid Host header".
3. ChatGPT: Settings → Apps & Connectors → Advanced → enable Developer mode, then create a connector with `https://<tunnel>/mcp` and no authentication. Claude: Settings → Connectors → Add custom connector with the same URL.
4. Ask for an outlook. Hover the map for the three probabilities, switch the method, click Explain for the model's reading, Expand for the fullscreen map. Repeated requests and the method switch are served from the result cache (`S2S_RECIPE_CACHE=0` disables it). A map drawn by `run_pipeline` shows as the same kind of card once the model calls `view_output` on its handle.

Forecast-year probabilities under `method="cca"` come from CPT's per-model error model (`probability_aggregation="cpt_per_model"` in africas2s), which stays well-defined with a single model. The other methods use africas2s's pooled aggregation, whose spread comes from disagreement between models; with one model it has none, and the map collapses to 0/1 probabilities until africas2s supports error-based probabilities for those methods.

`seasonal_outlook` defaults to the six NMME precipitation models acmaddl serves (CCSM4, CFSv2, GEOS-S2S, CanSIPS-IC4, SPEAR, CESM1); the first request for a region downloads each one, which took between half a minute and three minutes per model for an East Africa box. Hosts time tool calls out after roughly a minute, so warm the data cache for a region before demoing it: run `seasonal_outlook` once from any client, or from Python via `s2s_mcp.recipes.seasonal_outlook` (the same arguments produce the same cache key either way; the map `title` is not part of the key, since hosts such as ChatGPT add one of their own, and a hit under a new title only redraws the PNG). A run that outlives the call budget does not fail: `seasonal_outlook` runs the recipe in a background job and waits up to `wait_seconds` (default 45), then answers `{status: "running", request, elapsed_seconds, poll_after_seconds, message}` while the job carries on. The widget shows the card with a progress note and polls the same tool (`wait_seconds=10`) until the result arrives; a text-only agent collects it by calling again with the same arguments. Identical requests share one job, finished results go to the cache, and complete results carry `status: "done"`. Jobs live in the server process, so a multi-process deployment needs a shared queue in their place. Browser-based hosts such as the ext-apps `basic-host` example connect from the page itself and need CORS headers; this server does not add them, so put a CORS-enabling proxy in front for those (ChatGPT and Claude connect server-side and need nothing).

## Protocol

Implements MCP revision 2026-07-28 through the `mcp` 2.x SDK: stateless core, `server/discover`, cache freshness hints on list results, Streamable HTTP as the only network transport. Long-running calls will move to the Tasks extension once the Python SDK ships server-side support.

## Development

```bash
uv sync --extra dev
uv run pytest -q
cd web && npm install && npm test   # widget unit tests (vitest)
```

Tests use synthetic data and stub acmadDL's network verbs; the africas2s pipeline runs for real. Until the `[mcp]` extras are released on PyPI, `pyproject.toml` resolves the two libraries from their `main` branches; delete their `[tool.uv.sources]` entries after release so the PyPI pins take over.

The host also carries Rhiza's weather-skills provider packages, so acmadDL's `weather-skills/*` products (IFS 46-day, GEFS 35-day, CHC SubC outlooks, ERA5, IMERG, ...) fetch through `run_pipeline` and their transforms are importable. acmadDL keeps these in a uv dependency group, which does not propagate to a consumer, so `pyproject.toml` lists them directly with the three provider repos pinned to the same commits as acmadDL's. Bump those pins only together with acmadDL; `tests/test_pipeline.py` fails if the stack is missing.
