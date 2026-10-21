import json

import pytest
import xarray as xr
from mcp.server.mcpserver.exceptions import ToolError

from s2s_mcp import handles, pipeline
from tests.conftest import run


def test_operations_discovered_from_both_servers():
    names = set(pipeline.OPERATIONS)
    assert {"acmaddl.fetch", "acmaddl.zonal", "africas2s.downscale", "africas2s.skill",
            "africas2s.plot_terciles"} <= names
    assert not {"acmaddl.start_fetch", "acmaddl.fetch_status", "acmaddl.list_jobs"} & names
    assert pipeline.OPERATIONS["acmaddl.fetch"].accepts_destination
    assert pipeline.OPERATIONS["africas2s.list_registry"].accepts_destination is False
    assert pipeline.OPERATIONS["africas2s.downscale"].summary.startswith("Bias-correct")


def test_weather_skills_stack_is_installed_on_the_host():
    """acmadDL keeps Rhiza's weather-skills provider packages in a uv dependency
    group, which does not propagate here; the host lists them itself or every
    `weather-skills/*` fetch stops at WeatherSkillsNotInstalled. Guard all three
    links: the products are in the catalog the pipeline exposes, the provider
    packages import, and a credential-free product passes acmadDL's config
    health check (the skill loads and answers --help, offline)."""
    from acmaddl import weather_skills

    out = run(pipeline.run_pipeline([{"id": "p", "op": "acmaddl.list_products"}], "default"))
    products = {row["product"] for row in out["result"]["result"]}   # the SDK wraps a list result
    assert {"weather-skills/ifs-ens-46d", "weather-skills/gefs-35d", "weather-skills/ecmwf-s2s"} <= products

    assert {"dynamical-fetch", "ecmwf-fetch", "subc-mme-fetch"} <= set(weather_skills.skills(kind="fetchers"))

    health = run(pipeline.run_pipeline(
        [{"id": "h", "op": "acmaddl.check_product", "args": {"product": "weather-skills/ifs-ens-46d"}}], "default"))
    assert health["result"]["healthy"] is True, health["result"]
    assert health["result"]["kind"] == "config"


def test_resolve_references_and_handles(tmp_path):
    src = tmp_path / "f.nc"
    src.write_bytes(b"x")
    h = handles.mint("default", src, kind="dataset", label="f")["handle"]
    results = {"a": {"handle": h, "nested": {"list": [1, 2]}, "score": 0.5}}
    args = {"p": "$a.handle", "n": "$a.nested.list.1", "s": "$a.score", "direct": h, "plain": "text"}
    out = pipeline.resolve_args(args, results, "default")
    assert out["p"] == str(handles.resolve("default", h)) == out["direct"]
    assert out["n"] == 2 and out["s"] == 0.5 and out["plain"] == "text"
    with pytest.raises(ToolError, match="has not run yet"):
        pipeline.resolve_args("$zzz.x", results, "default")
    with pytest.raises(ToolError, match="not found in step"):
        pipeline.resolve_args("$a.missing", results, "default")


def test_pipeline_fetch_downscale_skill_plot(fake_acmaddl):
    steps = [
        {"id": "hind", "op": "acmaddl.fetch", "args": {"product": "nmme/ccsm4", "variable": "precip",
                                                        "init": "2025-02", "target": "MAM", "year_index": True}},
        {"id": "obs", "op": "acmaddl.fetch", "args": {"product": "obs/chirps-v3-monthly", "variable": "precip",
                                                       "target": "MAM", "seasonal": True}},
        {"id": "fc", "op": "africas2s.downscale", "args": {"predictor_hindcast_path": "$hind.handle",
                                                             "obs_path": "$obs.handle", "method": "bcsd",
                                                             "output_type": "tercile"}},
        {"id": "map", "op": "africas2s.plot_terciles", "args": {"probs_path": "$fc.handle", "title": "t"}},
    ]
    out = run(pipeline.run_pipeline(steps, "default", keep=["fc", "hind"]))
    assert [s["id"] for s in out["steps"]] == ["hind", "obs", "fc", "map"]
    assert all(s["status"] == "ok" and handles.is_handle(s["handle"]) for s in out["steps"])
    assert out["result"]["media_type"] == "image/png"
    assert handles.resolve("default", out["result"]["handle"]).read_bytes()[:4] == b"\x89PNG"
    fc = out["kept"]["fc"]
    assert fc["dims"]["tercile"] == 3 and fc["variables"] == {"probability": "1"}   # CF dimensionless
    assert "coords" not in fc  # brief
    assert fake_acmaddl["fetch"][0]["year_index"] is True
    assert handles.owned("default", fake_acmaddl["fetch"][0]["destination"]) == out["steps"][0]["handle"]
    json.dumps(out)


def test_pipeline_full_verbosity_and_errors(fake_acmaddl):
    steps = [{"id": "obs", "op": "acmaddl.fetch", "args": {"product": "obs/chirps-v3-monthly", "variable": "precip"}}]
    out = run(pipeline.run_pipeline(steps, "default", verbosity="full"))
    assert "coords" in out["result"] and "path" not in out["result"]

    with pytest.raises(ToolError, match="Unknown operation"):
        run(pipeline.run_pipeline([{"op": "acmaddl.nope"}], "default"))
    with pytest.raises(ToolError, match="Duplicate step id"):
        run(pipeline.run_pipeline([{"id": "a", "op": "africas2s.list_registry"},
                                   {"id": "a", "op": "africas2s.list_registry"}], "default"))
    with pytest.raises(ToolError, match="keep names unknown"):
        run(pipeline.run_pipeline([{"id": "a", "op": "africas2s.list_registry"}], "default", keep=["b"]))
    with pytest.raises(ToolError, match=r"(?s)Step 'ds' \(africas2s.downscale\) failed: .*Completed steps kept their outputs: \['obs -> out_"):
        run(pipeline.run_pipeline(steps + [
            {"id": "ds", "op": "africas2s.downscale", "args": {"predictor_hindcast_path": "$obs.handle",
                                                                 "obs_path": "$obs.handle", "method": "nope"}},
        ], "default"))


def test_pipeline_validates_arguments_like_a_direct_call(fake_acmaddl):
    with pytest.raises(ToolError, match="variable"):
        run(pipeline.run_pipeline([{"op": "acmaddl.fetch", "args": {"product": "obs/era5", "variable": "rain"}}], "default"))
    assert fake_acmaddl["fetch"] == []
