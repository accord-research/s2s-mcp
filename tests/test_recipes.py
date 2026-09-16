import json

import pytest
import xarray as xr
from mcp.server.mcpserver.exceptions import ToolError

from s2s_mcp import handles, recipes


def test_seasonal_outlook_end_to_end(fake_acmaddl):
    out = recipes.seasonal_outlook(
        tenant="default", models=["nmme/ccsm4"], predictand="obs/chirps-v3-monthly",
        variable="precip", init="2025-02", target="MAM", region=[-10, 4, 30, 40],
        hindcast=[1993, 2015], method="cca", cv_window=5,
    )
    json.dumps(out)
    assert out["models"] == ["ccsm4"]
    assert "rpss" in out["skill"]
    for key in ("tercile_forecast", "cv_hindcast", "deterministic", "tercile_map"):
        assert handles.is_handle(out[key]), key
    probs = xr.open_dataarray(handles.resolve("default", out["tercile_forecast"]))
    assert probs.dims == ("tercile", "lat", "lon")
    cv = xr.open_dataarray(handles.resolve("default", out["cv_hindcast"]))
    assert cv.dims[:2] == ("year", "tercile")
    assert handles.resolve("default", out["tercile_map"]).read_bytes()[:4] == b"\x89PNG"
    assert fake_acmaddl["assemble"][0]["roster"] == [("ccsm4", "nmme/ccsm4", (1993, 2015))]
    assert fake_acmaddl["fetch"][0]["seasonal"] == "mean"


def test_seasonal_outlook_validates_inputs():
    with pytest.raises(ToolError, match="at least one"):
        recipes.seasonal_outlook(tenant="default", models=[], predictand="obs/x", variable="precip",
                                 init="2025-02", target="MAM", region=[0, 1, 0, 1], hindcast=[1993, 2016])
    with pytest.raises(ToolError, match="hindcast must be"):
        recipes.seasonal_outlook(tenant="default", models=["m"], predictand="obs/x", variable="precip",
                                 init="2025-02", target="MAM", region=[0, 1, 0, 1], hindcast=[1993])


def test_seasonal_outlook_surfaces_fetch_failures(monkeypatch):
    import acmaddl

    def _boom(*a, **k):
        raise RuntimeError("CDS queue timeout")

    monkeypatch.setattr(acmaddl, "assemble", _boom)
    with pytest.raises(ToolError, match="Fetching the forecast models failed: RuntimeError: CDS queue timeout"):
        recipes.seasonal_outlook(tenant="default", models=["nmme/ccsm4"], predictand="obs/chirps-v3-monthly",
                                 variable="precip", init="2025-02", target="MAM", region=[0, 1, 0, 1],
                                 hindcast=[1993, 2016])
