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
- First runs take minutes (provider queues); repeats are cached on the server.

## Custom chains: run_pipeline

Steps are `{"id", "op", "args"}`. `op` names a primitive (`list_operations`), `args` follow its schema (`describe_operation`). Reference earlier outputs with `"$id.field"`; pass handles wherever a primitive wants a path. Only the last step's result returns (plus `keep` steps), trimmed unless `verbosity="full"`.

Data conventions the primitives expect (from africas2s): hindcast `(year, member, lat, lon)`, forecast `(member, lat, lon)`, observations `(year, lat, lon)`, tercile forecast `(tercile, lat, lon)` with `tercile=[0,1,2]` = below/normal/above. `acmaddl.fetch` with `year_index=true` yields the hindcast shape; with `target` and `seasonal=true` it yields seasonal observations.

Rules that bite: tercile metrics (`rpss`, `roc`) need tercile probabilities from cross-validation, never a fit scored on its own training years; `africas2s.calibrate` does not regrid (downscale methods do).

## Housekeeping

`list_outputs` shows your handles. `describe_dataset(handle)` shows dims, coordinates, and units. Handles belong to your tenant; another agency cannot read them.
