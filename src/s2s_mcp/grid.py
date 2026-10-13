"""Presentation payloads for the outlook widget.

The widget draws its own map from a probability grid rather than showing the
server's PNG. A full CHIRPS-resolution region is ~100k cells x 3 terciles,
too much to push through a tool result, so ``coarsen_probs`` block-averages
the field down to ``max_cells`` (presentation only; the files on disk are
untouched). ``tercile_style`` serialises africas2s' named palette so the
widget's colours match the server's maps exactly.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import xarray as xr

DIMS = ("tercile", "lat", "lon")


def _require_dims(da: xr.DataArray) -> xr.DataArray:
    if set(da.dims) != set(DIMS):
        raise ValueError(f"Expected a (tercile, lat, lon) probability field, got dims {tuple(da.dims)}.")
    return da.transpose(*DIMS)


def _edges(coord: np.ndarray) -> tuple[float, float]:
    """Outer edges of a 1-D centre coordinate (assumes regular spacing)."""
    coord = np.asarray(coord, dtype=float)
    if coord.size < 2:
        return float(coord[0]) - 0.5, float(coord[0]) + 0.5
    step = abs(float(coord[1] - coord[0]))
    return float(coord.min()) - step / 2, float(coord.max()) + step / 2


def bounds_of(da: xr.DataArray) -> list[float]:
    """``[lat_s, lat_n, lon_w, lon_e]`` outer edges of a gridded field."""
    lat_s, lat_n = _edges(da["lat"].values)
    lon_w, lon_e = _edges(da["lon"].values)
    return [lat_s, lat_n, lon_w, lon_e]


def _flat(values: np.ndarray) -> list[float | None]:
    out: list[float | None] = []
    for v in np.round(values.astype(float), 3).ravel():
        out.append(None if math.isnan(v) else float(v))
    return out


def coarsen_probs(da: xr.DataArray, max_cells: int = 20_000) -> dict[str, Any]:
    """Block-average a (tercile, lat, lon) field to at most ``max_cells`` cells.

    Returns ``{bounds, lat, lon, factor, below, normal, above}``; the three
    arrays are flat, row-major ``(lat, lon)`` with lat south-to-north, lon
    west-to-east, and ``None`` where the field is NaN (sea, missing).
    """
    da = _require_dims(da)
    n = da.sizes["lat"] * da.sizes["lon"]
    factor = max(1, math.ceil(math.sqrt(n / max(1, max_cells))))
    if factor > 1:
        da = da.coarsen(lat=factor, lon=factor, boundary="pad").mean()
    da = da.sortby("lat").sortby("lon")
    return {
        "bounds": bounds_of(da),
        "lat": [float(v) for v in da["lat"].values],
        "lon": [float(v) for v in da["lon"].values],
        "factor": factor,
        "below": _flat(da.isel(tercile=0).values),
        "normal": _flat(da.isel(tercile=1).values),
        "above": _flat(da.isel(tercile=2).values),
    }


def palette_note(variable: str = "precip") -> str:
    """One sentence telling a language model how the tercile map is coloured.

    Hosts hand the model the tool result, not the picture, and a model asked to
    describe the map will otherwise guess (ChatGPT confidently reported blue for
    wet and red for dry). Keep this in step with the ``acmad`` style.
    """
    above, below = ("wetter", "drier") if variable != "temp" else ("warmer", "cooler")
    return (f"Map colours: greens mark above-normal ({above}) cells, yellows through reds mark "
            f"below-normal ({below}) cells, greys mark near-normal; darker shades mean a higher "
            "probability (bins at 33, 36, 40, 50, 60 and 70%), and near-white cells have no clear "
            "signal (no tercile above 40%). There is no blue on the map.")


def tercile_style(name: str = "acmad") -> dict[str, Any]:
    """africas2s' named tercile palette as plain JSON for the widget."""
    from africas2s import TercileStyle

    style = TercileStyle.named(name)
    return {
        "name": name,
        "below_colors": [str(c) for c in style.below_colors],
        "normal_colors": [str(c) for c in style.normal_colors],
        "above_colors": [str(c) for c in style.above_colors],
        "prob_bins": [float(b) for b in style.prob_bins],
        "dry_color": str(style.dry_color),
        "nodata_color": str(style.nodata_color),
    }
