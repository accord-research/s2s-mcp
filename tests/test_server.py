import json

import pytest
from mcp.client import Client
from mcp.server.mcpserver.exceptions import ToolError

from mcp.client import advertise
from mcp.server.apps import APP_MIME_TYPE, EXTENSION_ID

from s2s_mcp import apps as _apps
from s2s_mcp import handles, server
from tests.conftest import run

DEFAULT_TOOLS = {"seasonal_outlook", "run_pipeline", "list_operations", "describe_operation",
                 "list_products", "describe_dataset", "list_outputs", "get_output", "outlook_grid"}


def test_default_surface_is_small():
    tools = run(server.mcp.list_tools())
    assert {t.name for t in tools} == DEFAULT_TOOLS
    total = sum(len(json.dumps({"n": t.name, "d": t.description, "s": t.input_schema})) for t in tools)
    assert total // 4 < 4000, f"~{total // 4} tokens of tool definitions; keep the default surface small"
    for t in tools:
        assert "ctx" not in t.input_schema["properties"], t.name
        assert "Returns:" in t.description, t.name
        assert all(p.get("description") for p in t.input_schema["properties"].values()), t.name


def test_protocol_2026_07_28_compliance():
    async def scenario():
        async with Client(server.mcp) as client:
            assert client.protocol_version == "2026-07-28"
            assert client.server_info.name == "s2s" and client.server_info.website_url
            tools = await client.list_tools()
            assert tools.ttl_ms > 0 and tools.cache_scope == "public" and tools.result_type == "complete"
            ops = await client.call_tool("list_operations", {})
            assert not ops.is_error
            read = await client.read_resource("s2s://skill")
            assert read.contents and "seasonal_outlook" in read.contents[0].text

    run(scenario())


def test_discovery_tools():
    ops = server.list_operations()
    assert any(o["op"] == "africas2s.downscale" for o in ops)
    desc = server.describe_operation("africas2s.downscale")
    assert desc["input_schema"]["properties"]["method"]["enum"]
    with pytest.raises(ToolError, match="Unknown operation"):
        server.describe_operation("nope")
    assert server.list_products()[0]["product"]


def test_outputs_roundtrip(fake_acmaddl):
    obs = run(server.run_pipeline([
        {"id": "obs", "op": "acmaddl.fetch", "args": {"product": "obs/chirps-v3-monthly", "variable": "precip"}},
    ]))
    h = obs["result"]["handle"]
    described = server.describe_dataset(h)
    assert described["handle"] == h and described["dims"]["year"] == 23 and "path" not in described
    listed = server.list_outputs()
    assert [o["handle"] for o in listed] == [h]
    blocks = server.get_output(h)
    info = json.loads(blocks[0].text)
    assert info["download_url"] is None and info["path"].endswith(".nc")

    with pytest.raises(ToolError, match=r"Step 'map' \(africas2s.plot_field\) failed: .*2-D"):
        run(server.run_pipeline([
            {"id": "map", "op": "africas2s.plot_field", "args": {"path": h, "variable": "precip"}},
        ]))

    png = run(server.run_pipeline([
        {"id": "hind", "op": "acmaddl.fetch", "args": {"product": "nmme/ccsm4", "variable": "precip",
                                                        "init": "2025-02", "target": "MAM", "year_index": True}},
        {"id": "fc", "op": "africas2s.downscale", "args": {"predictor_hindcast_path": "$hind.handle", "obs_path": h,
                                                            "method": "climatology", "output_type": "tercile"}},
        {"id": "map", "op": "africas2s.plot_terciles", "args": {"probs_path": "$fc.handle"}},
    ]))
    image = server.get_output(png["result"]["handle"])[0]
    assert image.type == "image" and image.mime_type == "image/png" and len(image.data) > 100


def test_download_url_when_public_base_configured(fake_acmaddl, monkeypatch):
    monkeypatch.setenv("S2S_PUBLIC_BASE_URL", "https://s2s.example.org/")
    obs = run(server.run_pipeline([
        {"id": "obs", "op": "acmaddl.fetch", "args": {"product": "obs/chirps-v3-monthly", "variable": "precip"}},
    ]))
    info = json.loads(server.get_output(obs["result"]["handle"])[0].text)
    assert info["download_url"] == f"https://s2s.example.org/outputs/default/{obs['result']['handle']}"


def test_http_app_serves_downloads(fake_acmaddl):
    from starlette.testclient import TestClient

    obs = run(server.run_pipeline([
        {"id": "obs", "op": "acmaddl.fetch", "args": {"product": "obs/chirps-v3-monthly", "variable": "precip"}},
    ]))
    h = obs["result"]["handle"]
    app = server.build_http_app(stateless=True, json_response=True, host="127.0.0.1")
    with TestClient(app) as http:
        assert http.get(f"/outputs/default/{h}").status_code == 200
        assert http.get("/outputs/default/out_000000000000").status_code == 404
        assert http.get(f"/outputs/other/{h}").status_code == 403


def test_seasonal_outlook_tool_checks_products(fake_acmaddl):
    with pytest.raises(ToolError, match="Product not found"):
        server.seasonal_outlook(init="2025-02", target="MAM", region=[-10, 4, 30, 40], models=["nmme/nope"])


def test_advanced_mode_registers_primitives(monkeypatch):
    monkeypatch.setenv("S2S_TENANT_MODE", "header")
    with pytest.raises(RuntimeError, match="single"):
        server.register_primitives()
    monkeypatch.setenv("S2S_TENANT_MODE", "single")
    names = server.register_primitives()
    assert "acmaddl_fetch" in names and "africas2s_downscale" in names
    tools = {t.name: t for t in run(server.mcp.list_tools())}
    assert tools["africas2s_downscale"].input_schema["properties"]["method"]["enum"]


def test_main_flags(monkeypatch):
    seen = {}
    monkeypatch.setattr(server.mcp, "run", lambda transport, **kw: seen.update(t=transport, **kw))
    server.main([])
    assert seen["t"] == "stdio"
    with pytest.raises(SystemExit):
        server.main(["--transport", "sse"])


def test_outlook_tool_is_an_mcp_app():
    tools = {t.name: t for t in run(server.mcp.list_tools())}
    meta = tools["seasonal_outlook"].meta
    assert meta["ui"] == {"resourceUri": "ui://s2s/outlook"}
    assert meta["openai/outputTemplate"] == "ui://s2s/outlook"
    assert meta["openai/widgetAccessible"] is True
    assert meta["openai/toolInvocation/invoking"] and meta["openai/toolInvocation/invoked"]
    assert tools["outlook_grid"].meta == {"ui": {"visibility": ["app"]}}
    assert "two-to-three sentence" in tools["seasonal_outlook"].description


def test_outlook_resource_is_served_as_app():
    async def scenario():
        async with Client(server.mcp, extensions=[advertise(EXTENSION_ID, {"mimeTypes": [APP_MIME_TYPE]})]) as client:
            assert EXTENSION_ID in client.server_capabilities.extensions
            listed = {str(r.uri): r for r in (await client.list_resources()).resources}
            assert listed["ui://s2s/outlook"].mime_type == APP_MIME_TYPE
            assert listed["ui://s2s/outlook"].meta == {"ui": {"prefersBorder": True}}
            read = await client.read_resource("ui://s2s/outlook")
            body = read.contents[0]
            assert body.mime_type == APP_MIME_TYPE
            assert body.text.lstrip().lower().startswith("<!doctype html>")

    run(scenario())


def _outlook(**over):
    args = dict(init="2025-02", target="MAM", region=[-10, 4, 30, 40], models=["nmme/ccsm4"],
                hindcast=[1993, 2015], method="climatology")
    args.update(over)
    return server.seasonal_outlook(**args)


def test_outlook_grid_roundtrip(fake_acmaddl):
    out = _outlook()
    payload = server.outlook_grid(out["tercile_forecast"], max_cells=50)
    assert payload["handle"] == out["tercile_forecast"]
    assert payload["factor"] == 2 and len(payload["lat"]) == 6 and len(payload["below"]) == 36
    assert payload["bounds"] == pytest.approx(out["bounds"])
    assert payload["style"]["name"] == "acmad" and len(payload["style"]["prob_bins"]) == 7
    json.dumps(payload)


def test_outlook_grid_rejects_wrong_dims(fake_acmaddl):
    out = _outlook()
    with pytest.raises(ToolError, match=r"\(tercile, lat, lon\)"):
        server.outlook_grid(out["deterministic"])
    with pytest.raises(ToolError, match="not an output handle"):
        server.outlook_grid("nope")


def test_widget_html_placeholder_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(_apps, "OUTLOOK_HTML", tmp_path / "missing.html")
    html = _apps.widget_html()
    assert html.lower().startswith("<!doctype html>") and "npm run build" in html


def test_built_widget_is_shipped():
    html = _apps.OUTLOOK_HTML.read_text(encoding="utf-8")
    assert html.lower().startswith("<!doctype html>")
    assert "npm run build" not in html, "placeholder is still checked in; run npm run build in web/"
    assert '<script type="module"' in html
    assert 'src="http' not in html and 'href="http' not in html     # no external origins (CSP is deny-by-default)
    assert _apps.OUTLOOK_HTML.stat().st_size < 1_000_000
    assert (_apps.WEB_DIR / "africa-countries.geojson").stat().st_size > 10_000


def test_outlook_grid_multivariable_error_names_handle_not_path():
    import numpy as np
    import xarray as xr

    handle, path = handles.reserve("default", ".nc")
    xr.Dataset({"a": (("lat", "lon"), np.zeros((2, 2))), "b": (("lat", "lon"), np.zeros((2, 2)))},
               coords={"lat": [0.0, 1.0], "lon": [0.0, 1.0]}).to_netcdf(path)
    handles.register("default", handle, kind="dataset", label="t")
    with pytest.raises(ToolError) as err:
        server.outlook_grid(handle)
    assert handle in str(err.value) and "single" in str(err.value)
    assert str(path) not in str(err.value) and str(path.parent) not in str(err.value)


NMME_SIX = ["nmme/ccsm4", "nmme/cfsv2", "nmme/geoss2s", "nmme/cansipsic4", "nmme/spear", "nmme/cesm1"]


def test_outlook_defaults_to_six_nmme_models():
    """A single model gives africas2s's pooled tercile path no spread (0/1 maps);
    the default roster is every NMME precipitation model acmaddl serves."""
    tools = {t.name: t for t in run(server.mcp.list_tools())}
    assert tools["seasonal_outlook"].input_schema["properties"]["models"]["default"] == NMME_SIX


def test_outlook_tool_returns_running_for_slow_runs_then_done(monkeypatch, fake_acmaddl):
    """A cold run outlives the host's call budget: the tool hands back a
    `running` status instead of timing out, and a later call collects the result."""
    import threading

    from s2s_mcp import recipes

    gate = threading.Event()
    real = recipes.seasonal_outlook

    def slow(**kw):
        gate.wait(10)
        return real(**kw)

    monkeypatch.setattr(recipes, "seasonal_outlook", slow)
    first = _outlook(wait_seconds=0)
    assert first["status"] == "running"
    assert first["request"]["method"] == "climatology" and first["poll_after_seconds"] >= 1
    assert "tercile_forecast" not in first
    gate.set()
    second = _outlook(wait_seconds=30)
    assert second["status"] == "done" and handles.is_handle(second["tercile_forecast"])


def test_outlook_tool_marks_complete_results_done(fake_acmaddl):
    assert _outlook()["status"] == "done"
