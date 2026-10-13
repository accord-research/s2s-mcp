import math

import numpy as np
import pytest
import xarray as xr

from s2s_mcp import grid


def probs(nlat=12, nlon=12, descending=False):
    lat = np.linspace(-10, 4, nlat)
    lon = np.linspace(30, 40, nlon)
    if descending:
        lat = lat[::-1]
    rng = np.random.default_rng(0)
    p = rng.dirichlet([1, 1, 1], size=(nlat, nlon)).transpose(2, 0, 1)
    da = xr.DataArray(p, dims=("tercile", "lat", "lon"),
                      coords={"tercile": [0, 1, 2], "lat": lat, "lon": lon}, name="probability")
    da[:, 0, 0] = np.nan
    return da


def test_native_grid_passthrough():
    out = grid.coarsen_probs(probs(), max_cells=1000)
    assert out["factor"] == 1
    assert len(out["lat"]) == 12 and len(out["lon"]) == 12
    assert len(out["below"]) == 144
    assert out["below"][0] is None and out["normal"][0] is None and out["above"][0] is None
    s = out["below"][5] + out["normal"][5] + out["above"][5]
    assert math.isclose(s, 1.0, abs_tol=2e-3)


def test_coarsen_factor_and_shape():
    out = grid.coarsen_probs(probs(nlat=40, nlon=60), max_cells=200)
    assert out["factor"] == 4            # ceil(sqrt(2400 / 200)) = 4
    assert len(out["lat"]) == 10 and len(out["lon"]) == 15
    assert len(out["above"]) == 150
    assert all(v is None or 0.0 <= v <= 1.0 for v in out["above"])


def test_bounds_are_outer_edges():
    out = grid.coarsen_probs(probs(), max_cells=1000)
    step_lat = 14 / 11
    step_lon = 10 / 11
    lat_s, lat_n, lon_w, lon_e = out["bounds"]
    assert math.isclose(lat_s, -10 - step_lat / 2) and math.isclose(lat_n, 4 + step_lat / 2)
    assert math.isclose(lon_w, 30 - step_lon / 2) and math.isclose(lon_e, 40 + step_lon / 2)
    assert grid.bounds_of(probs()) == pytest.approx(out["bounds"])


def test_coarsen_handles_descending_lat():
    out = grid.coarsen_probs(probs(descending=True), max_cells=1000)
    assert out["lat"] == sorted(out["lat"])           # always south to north
    assert out["bounds"] == pytest.approx(grid.coarsen_probs(probs(), max_cells=1000)["bounds"])


def test_rejects_wrong_dims():
    da = probs().isel(tercile=0, drop=True)
    with pytest.raises(ValueError, match=r"\(tercile, lat, lon\)"):
        grid.coarsen_probs(da)


def test_tercile_style_payload():
    style = grid.tercile_style()
    assert style["name"] == "acmad"
    assert len(style["below_colors"]) == len(style["normal_colors"]) == len(style["above_colors"]) == 6
    assert style["prob_bins"][0] == pytest.approx(33.33) and style["prob_bins"][-1] > 100
    assert style["dry_color"].startswith("#") and style["nodata_color"].startswith("#")
