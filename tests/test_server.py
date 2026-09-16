import json

import pytest
from mcp.client import Client
from mcp.server.mcpserver.exceptions import ToolError

from s2s_mcp import handles, server
from tests.conftest import run

DEFAULT_TOOLS = {"seasonal_outlook", "run_pipeline", "list_operations", "describe_operation",
                 "list_products", "describe_dataset", "list_outputs", "get_output"}


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
