"""Recipes: ACMAD's standard workflows as single calls.

A recipe is the in-process equivalent of a pipeline an expert would submit:
fetch the predictor models and the predictand, run the multi-model seasonal
pipeline, verify it honestly, draw the map, and return handles plus the skill
table. It calls the two libraries' public verbs directly (no MCP round trips,
no intermediate summaries), so a thin agent gets a finished product from one
tool call. Recipes contain no science of their own: every step is a
documented library call, in the order the africas2s end-to-end example uses.
"""

from __future__ import annotations

import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

import acmaddl
import africas2s
import xarray as xr

from . import cache, grid, handles, highlights


def _label(product: str) -> str:
    return product.split("/", 1)[-1]


def _json_safe(obj: Any) -> Any:
    import numpy as np

    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, float) and obj != obj:
        return None
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def normalised_request(*, models: list[str], predictand: str, variable: str, init: str, target: str,
                       region: list[float] | str, hindcast: list[int], method: str = "cca", cv_window: int = 5,
                       title: str | None = None) -> dict[str, Any]:
    """The request as the cache, the job registry and the widget see it: int-vs-float
    spelling of the same numbers folded, so equal requests share one key."""
    return {"models": list(models), "predictand": predictand, "variable": variable, "init": init,
            "target": target,
            "region": [float(v) for v in region] if isinstance(region, (list, tuple)) else region,
            "hindcast": [int(v) for v in hindcast],
            "method": method, "cv_window": int(cv_window), "title": title}


def outlook_key(**kwargs: Any) -> str:
    """Cache/job key of a ``seasonal_outlook`` request (its title is not part of it)."""
    return cache.key(_cache_key_args(normalised_request(**kwargs)))


def _cache_key_args(request: dict[str, Any]) -> dict[str, Any]:
    """The request minus ``title``: the title changes nothing but the PNG heading."""
    return {k: v for k, v in request.items() if k != "title"}


def _render_map(da, path: Path, title: str, variable: str) -> str | None:
    """Draw the tercile PNG to ``path``; returns an error string instead of raising."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    try:
        africas2s.plot_terciles(da, title=title, variable_kind="temp" if variable == "temp" else "precip")
        plt.gcf().savefig(path, dpi=150, bbox_inches="tight")
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"
    finally:
        plt.close("all")
    return None


def _retitled(tenant: str, key_args: dict[str, Any], cached: dict[str, Any], title: str | None, map_title: str,
              variable: str, png: Path, label: str, origin: dict[str, Any]) -> dict[str, Any]:
    """A cache hit whose PNG was drawn under another title: redraw the PNG only,
    and remember the result so repeats under this title are plain hits."""
    out = {**cached, "request": {**cached["request"], "title": title}}
    try:
        da = xr.open_dataarray(handles.resolve(tenant, cached["tercile_forecast"])).load()
        map_error = _render_map(da, png, map_title, variable)
    except Exception as exc:  # noqa: BLE001
        map_error = f"{type(exc).__name__}: {exc}"
    if map_error:
        out["map_error"] = map_error      # keep the previous PNG rather than none at all
    else:
        out["tercile_map"] = handles.mint(tenant, png, kind="image", label=label,
                                          origin={**origin, "title": title})["handle"]
        cache.put(tenant, key_args, out)
    return out


def _write_nc(da, path: Path, name: str):
    da = da.rename(name) if da.name != name else da
    ds = da.to_dataset(name=name)
    for coord in list(ds.coords):
        if ds[coord].dtype == object:
            ds[coord] = ds[coord].astype(str)
    ds.to_netcdf(path)
    return path


def seasonal_outlook(
    *,
    tenant: str,
    models: list[str],
    predictand: str,
    variable: str,
    init: str,
    target: str,
    region: list[float] | str,
    hindcast: list[int],
    method: str = "cca",
    cv_window: int = 5,
    title: str | None = None,
) -> dict[str, Any]:
    """Fetch, downscale/calibrate, verify, and map one seasonal outlook."""
    if not models:
        raise ToolError("models must name at least one forecast product.")
    if len(hindcast) != 2:
        raise ToolError("hindcast must be [start_year, end_year].")
    request = normalised_request(models=models, predictand=predictand, variable=variable, init=init, target=target,
                                 region=region, hindcast=hindcast, method=method, cv_window=cv_window, title=title)
    window = (int(hindcast[0]), int(hindcast[1]))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    base = f"outlook_{_label(predictand)}_{target}_{init}_{stamp}"
    origin = {"recipe": "seasonal_outlook", "models": models, "predictand": predictand,
              "variable": variable, "init": init, "target": target, "hindcast": list(window),
              "method": method, "cv_window": cv_window}
    map_title = title or f"{target} {init[:4]} {variable}: {', '.join(_label(m) for m in models)} ({method})"

    def _reserve(suffix: str) -> Path:
        return handles.reserve(tenant, suffix)[1]

    # The title only appears on the PNG, so it is not part of the cache key: hosts
    # add titles of their own (ChatGPT does, unasked) and must still hit the cache.
    key_args = _cache_key_args(request)
    cached = cache.get(tenant, key_args)
    if cached is not None:
        if cached.get("request", {}).get("title") == title:
            return cached
        return _retitled(tenant, key_args, cached, title, map_title, variable, _reserve(".png"), f"{base} tercile_map", origin)

    # 1. Predictor tracks: {label: (hindcast, forecast)} on (year, member, lat, lon).
    roster = [(_label(p), p, window) for p in models]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            tracks = acmaddl.assemble(roster, variable, init=init, target=target,
                                      region=region, verbose=False)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Fetching the forecast models failed: {type(exc).__name__}: {exc}") from exc

        # 2. Predictand: seasonal mean per year on the fine grid, (year, lat, lon).
        try:
            obs_ds = acmaddl.fetch(predictand, variable, region=region, hindcast=window,
                                   target=target, seasonal="mean", verbose=False, progress=False)
        except NotImplementedError as exc:
            raise ToolError(f"Predictand for a wrap-around season ({target}) is not supported yet: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Fetching the predictand failed: {type(exc).__name__}: {exc}") from exc
    obs = obs_ds[variable] if hasattr(obs_ds, "data_vars") else obs_ds

    # 3. Multi-model seasonal pipeline: CV fit/predict per model, pooled ensemble,
    #    tercile forecast + CV terciles + skill.
    #    Forecast-year probabilities: the pooled aggregation measures spread across
    #    models, so with a single model it collapses to 0/1. Under CCA the library
    #    can instead derive them from the regression error (CPT's Student-t path);
    #    it rejects that mode for other methods, which therefore stay pooled.
    aggregation = "cpt_per_model" if method == "cca" else "pooled"
    try:
        result = africas2s.seasonal_mme(
            {variable: tracks}, obs, method=method, cv="loyo",
            cpt_args={"crossvalidation_window": int(cv_window)},
            probability_aggregation=aggregation, verbose=False,
        )
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"seasonal_mme failed: {type(exc).__name__}: {exc}") from exc

    # 4. Outputs.
    files = {
        "tercile_forecast": _write_nc(result.tercile_forecast, _reserve(".nc"), "probability"),
        "cv_hindcast": _write_nc(result.tercile_cv, _reserve(".nc"), "probability"),
        "deterministic": _write_nc(result.forecast, _reserve(".nc"), variable),
    }
    png = _reserve(".png")
    map_error = _render_map(result.tercile_forecast, png, map_title, variable)
    files["tercile_map"] = None if map_error else png

    skill = _json_safe(result.skill_report.to_dict().get("scores", {}))
    out: dict[str, Any] = {
        "models": [_label(m) for m in models],
        "skill": skill,
        "metadata": _json_safe(result.metadata),
        "request": request,
    }
    # Presentation steps must never discard a finished pipeline run: on failure
    # fall back, record the error, and skip the cache so the next call retries.
    try:
        out["bounds"] = grid.bounds_of(result.tercile_forecast)
    except Exception as exc:  # noqa: BLE001
        out["bounds"] = [float(v) for v in region] if isinstance(region, (list, tuple)) and len(region) == 4 else None
        out["bounds_error"] = f"{type(exc).__name__}: {exc}"
    for key, path in files.items():
        if path is None:
            continue
        kind = "image" if key == "tercile_map" else "dataset"
        out[key] = handles.mint(tenant, path, kind=kind, label=f"{base} {key}", origin=origin)["handle"]
    if map_error:
        out["map_error"] = map_error
    # Computed highlights: what the card shows and what the model's reading is grounded in.
    try:
        out["highlights"] = _json_safe(highlights.compute(result.tercile_forecast, skill))
    except Exception as exc:  # noqa: BLE001
        out["highlights"] = highlights.unavailable(skill, f"{type(exc).__name__}: {exc}")
        out["highlights_error"] = f"{type(exc).__name__}: {exc}"
    out["summary"] = out["highlights"]["summary"]
    out["palette"] = grid.palette_note(variable)
    if not any(k in out for k in ("map_error", "bounds_error", "highlights_error")):
        cache.put(tenant, key_args, out)
    return out
