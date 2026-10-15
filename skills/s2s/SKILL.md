---
name: s2s
description: Run seasonal (S2S) climate forecasts through the hosted s2s MCP server. Use when an agent needs a seasonal outlook (tercile probability map plus skill scores) for a region, or a custom chain of data fetch, downscaling, calibration, ensembling, verification, and plotting, without running code or holding data locally.
license: MIT
metadata:
  author: accord-research
  package: s2s-mcp
---

# s2s — hosted seasonal forecasting for thin agents

The server composes acmadDL (data) and africas2s (forecasting). You never see files: every output is a handle `out_xxxxxxxxxxxx`, and `get_output(handle)` shows a map inline or gives a download link for data.

## The fast path: one call

```
seasonal_outlook(init="2025-02", target="MAM", region=[-12, 6, 28, 42],
                 models=["c3s/ecmwf-monthly", "nmme/ccsm4"], predictand="obs/chirps-v3-monthly")
```

Returns handles for the tercile map (PNG), the tercile forecast `(tercile, lat, lon)`, the cross-validated hindcast `(year, tercile, lat, lon)`, the deterministic forecast, plus `skill` (rpss and friends, computed on held-out years) and `metadata`. Then `get_output(tercile_map)`.

- `region` is `[lat_south, lat_north, lon_west, lon_east]`.
- `init` is the issuance month; `target` the season (`MAM`, `JJAS`, `OND`). Wrap-around seasons (`DJF`, `NDJ`) are not supported for the predictand yet.
- `models` are forecast product ids and `predictand` an `obs/...` product id, both from `list_products`.
- `method="cca"` is the PyCPT-style default; `"climatology"` is the no-skill baseline to compare against.
- `seasonal_outlook` may answer `status: "running"` when a first run for a region outlives the call: tell the user it is computing, then call again with the same arguments after `poll_after_seconds` (`wait_seconds` up to 55 per call) until `status: "done"`.
- `models` defaults to the six NMME precipitation models; keep several models unless the user names one. With a single model, only `method="cca"` yields graded forecast-year probabilities; the other methods pool across models and return 0/1 maps when there is one.
- First runs take minutes (provider queues); repeats are cached on the server.

The result also carries `bounds` (`[lat_s, lat_n, lon_w, lon_e]` of the forecast grid), `request` (the arguments used, so a variant is one field away), `highlights` (area shares of the dominant tercile for the region and per country, the strongest wet and dry signals, a one-word skill verdict, a `region_label`) and `summary` (one sentence built from the highlights). Use the highlights when describing the outlook; they are computed, not read off the map. The same request twice returns the cached result instantly.

In hosts with MCP Apps the result renders as an interactive map; `outlook_grid` exists for that widget and is not meant for agents.

## Custom chains: run_pipeline

Steps are `{"id", "op", "args"}`. `op` names a primitive (`list_operations`), `args` follow its schema (`describe_operation`). Reference earlier outputs with `"$id.field"`; pass handles wherever a primitive wants a path. Only the last step's result returns (plus `keep` steps), trimmed unless `verbosity="full"`.

Data conventions the primitives expect (from africas2s): hindcast `(year, member, lat, lon)`, forecast `(member, lat, lon)`, observations `(year, lat, lon)`, tercile forecast `(tercile, lat, lon)` with `tercile=[0,1,2]` = below/normal/above. `acmaddl.fetch` with `year_index=true` yields the hindcast shape; with `target` and `seasonal=true` it yields seasonal observations.

Rules that bite: tercile metrics (`rpss`, `roc`) need tercile probabilities from cross-validation, never a fit scored on its own training years; `africas2s.calibrate` does not regrid (downscale methods do).

## Showing a map

After a pipeline step that draws a PNG (`africas2s.plot_field`, `africas2s.plot_terciles`), call `view_output(handle)` so the user sees the map as a card instead of a link. `get_output` remains the way to fetch data files.

## Housekeeping

`list_outputs` shows your handles. `describe_dataset(handle)` shows dims, coordinates, and units. Handles belong to your tenant; another agency cannot read them.
