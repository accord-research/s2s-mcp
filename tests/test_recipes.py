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


def _args(**over):
    base = dict(tenant="default", models=["nmme/ccsm4"], predictand="obs/chirps-v3-monthly",
                variable="precip", init="2025-02", target="MAM", region=[-10, 4, 30, 40],
                hindcast=[1993, 2015], method="cca", cv_window=5)
    base.update(over)
    return base


def test_seasonal_outlook_widget_fields(fake_acmaddl):
    out = recipes.seasonal_outlook(**_args())
    assert out["request"] == {k: v for k, v in _args().items() if k != "tenant"} | {"title": None}
    lat_s, lat_n, lon_w, lon_e = out["bounds"]
    assert lat_s < -10 < 4 < lat_n and lon_w < 30 < 40 < lon_e
    assert set(out["highlights"]) >= {"region_shares", "countries", "strongest", "skill_verdict", "region_label", "summary"}
    assert out["summary"] == out["highlights"]["summary"]
    assert "cross-validated skill" in out["summary"]
    assert "green" in out["palette"] and "above-normal" in out["palette"]   # the model reads colours from here, not from memory


def test_seasonal_outlook_is_cached(fake_acmaddl):
    first = recipes.seasonal_outlook(**_args())
    assert len(fake_acmaddl["assemble"]) == 1
    second = recipes.seasonal_outlook(**_args())
    assert second == first
    assert len(fake_acmaddl["assemble"]) == 1            # served from cache
    third = recipes.seasonal_outlook(**_args(method="climatology"))
    assert third["tercile_forecast"] != first["tercile_forecast"]
    assert len(fake_acmaddl["assemble"]) == 2


def test_seasonal_outlook_cache_can_be_disabled(fake_acmaddl, monkeypatch):
    monkeypatch.setenv("S2S_RECIPE_CACHE", "0")
    recipes.seasonal_outlook(**_args())
    recipes.seasonal_outlook(**_args())
    assert len(fake_acmaddl["assemble"]) == 2


def _raise(msg):
    def _f(*a, **k):
        raise RuntimeError(msg)
    return _f


def test_failed_map_is_not_cached(fake_acmaddl, monkeypatch):
    import africas2s

    monkeypatch.setattr(africas2s, "plot_terciles", _raise("no cartopy"))
    first = recipes.seasonal_outlook(**_args())
    assert "no cartopy" in first["map_error"] and "tercile_map" not in first
    assert handles.is_handle(first["tercile_forecast"])
    recipes.seasonal_outlook(**_args())
    assert len(fake_acmaddl["assemble"]) == 2            # incomplete results are recomputed


def test_highlights_failure_keeps_handles(fake_acmaddl, monkeypatch):
    monkeypatch.setattr(recipes.highlights, "compute", _raise("bad geojson"))
    out = recipes.seasonal_outlook(**_args())
    assert handles.is_handle(out["tercile_forecast"]) and handles.is_handle(out["tercile_map"])
    assert len(out["bounds"]) == 4
    assert out["highlights"]["region_shares"] == {"below": 0.0, "normal": 0.0, "above": 0.0, "none": 0.0}
    assert "bad geojson" in out["highlights_error"] and out["summary"] == out["highlights"]["summary"]
    recipes.seasonal_outlook(**_args())
    assert len(fake_acmaddl["assemble"]) == 2


def test_cache_key_is_insensitive_to_int_float_region(fake_acmaddl):
    recipes.seasonal_outlook(**_args(region=[-10, 4, 30, 40], hindcast=[1993, 2015], cv_window=5))
    recipes.seasonal_outlook(**_args(region=[-10.0, 4.0, 30.0, 40.0], hindcast=[1993.0, 2015.0], cv_window=5.0))
    assert len(fake_acmaddl["assemble"]) == 1            # same request, one computation


def test_seasonal_outlook_asks_for_per_model_probabilities_under_cca(fake_acmaddl, monkeypatch):
    """With one model, pooled aggregation has no member spread and yields 0/1
    'probabilities'; cpt_per_model derives them from the regression error.
    The library only supports it for CCA, so other methods stay pooled."""
    import africas2s

    calls = []
    real = africas2s.seasonal_mme

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(africas2s, "seasonal_mme", spy)
    monkeypatch.setenv("S2S_RECIPE_CACHE", "0")
    recipes.seasonal_outlook(**_args(method="cca"))
    recipes.seasonal_outlook(**_args(method="bcsd"))
    assert [c.get("probability_aggregation") for c in calls] == ["cpt_per_model", "pooled"]


def test_title_does_not_defeat_the_cache(fake_acmaddl):
    """Hosts add their own map title (ChatGPT sends one unasked); it must not
    turn a cached outlook into an 80 s recompute. Only the PNG carries the
    title, so a hit with a new title re-renders the PNG and nothing else."""
    first = recipes.seasonal_outlook(**_args(title="Outlook A"))
    second = recipes.seasonal_outlook(**_args(title="Outlook B"))
    assert len(fake_acmaddl["assemble"]) == 1                      # pipeline ran once
    assert second["request"]["title"] == "Outlook B"
    assert second["tercile_forecast"] == first["tercile_forecast"]  # same data
    assert second["tercile_map"] != first["tercile_map"]            # fresh PNG for the new title
    assert handles.resolve("default", second["tercile_map"]).read_bytes()[:4] == b"\x89PNG"
    third = recipes.seasonal_outlook(**_args(title="Outlook B"))
    assert third["tercile_map"] == second["tercile_map"]            # same title: no re-render
