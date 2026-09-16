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

from . import handles


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
    window = (int(hindcast[0]), int(hindcast[1]))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    base = f"outlook_{_label(predictand)}_{target}_{init}_{stamp}"

    def _reserve(suffix: str) -> Path:
        return handles.reserve(tenant, suffix)[1]

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
    try:
        result = africas2s.seasonal_mme(
            {variable: tracks}, obs, method=method, cv="loyo",
            cpt_args={"crossvalidation_window": int(cv_window)}, verbose=False,
        )
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"seasonal_mme failed: {type(exc).__name__}: {exc}") from exc

    # 4. Outputs.
    origin = {"recipe": "seasonal_outlook", "models": models, "predictand": predictand,
              "variable": variable, "init": init, "target": target, "hindcast": list(window),
              "method": method, "cv_window": cv_window}
    files = {
        "tercile_forecast": _write_nc(result.tercile_forecast, _reserve(".nc"), "probability"),
        "cv_hindcast": _write_nc(result.tercile_cv, _reserve(".nc"), "probability"),
        "deterministic": _write_nc(result.forecast, _reserve(".nc"), variable),
    }
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    map_title = title or f"{target} {init[:4]} {variable}: {', '.join(_label(m) for m in models)} ({method})"
    try:
        africas2s.plot_terciles(result.tercile_forecast, title=map_title,
                                variable_kind="temp" if variable == "temp" else "precip")
        png = _reserve(".png")
        plt.gcf().savefig(png, dpi=150, bbox_inches="tight")
        plt.close("all")
        files["tercile_map"] = png
    except Exception as exc:  # noqa: BLE001
        plt.close("all")
        files["tercile_map"] = None
        map_error = f"{type(exc).__name__}: {exc}"
    else:
        map_error = None

    out: dict[str, Any] = {
        "models": [_label(m) for m in models],
        "skill": _json_safe(result.skill_report.to_dict().get("scores", {})),
        "metadata": _json_safe(result.metadata),
    }
    for key, path in files.items():
        if path is None:
            continue
        kind = "image" if key == "tercile_map" else "dataset"
        out[key] = handles.mint(tenant, path, kind=kind, label=f"{base} {key}", origin=origin)["handle"]
    if map_error:
        out["map_error"] = map_error
    return out
