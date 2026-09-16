import pytest
from mcp.server.mcpserver.exceptions import ToolError

from s2s_mcp import handles
from s2s_mcp.tenancy import tenant_dir, tenant_from_headers


def test_reserve_register_and_resolve():
    handle, path = handles.reserve("default", ".nc")
    assert handles.is_handle(handle) and path.parent == tenant_dir("default")
    with pytest.raises(ToolError, match="no file to register"):
        handles.register("default", handle, kind="dataset", label="empty")
    path.write_bytes(b"data")
    h = handles.register("default", handle, kind="dataset", label="test")
    assert h["media_type"] == "application/x-netcdf" and h["size_bytes"] == 4
    assert handles.owned("default", path) == handle and handles.owned("other", path) is None
    path = handles.resolve("default", h["handle"])
    assert path.read_bytes() == b"data" and path.parent == tenant_dir("default")
    assert handles.describe("default", h["handle"])["label"] == "test"
    assert [o["handle"] for o in handles.listing("default")] == [h["handle"]]


def test_handles_are_tenant_scoped(tmp_path):
    src = tmp_path / "a.png"
    src.write_bytes(b"\x89PNG")
    h = handles.mint("kenya", src, kind="image", label="map")
    assert handles.resolve("kenya", h["handle"]).suffix == ".png"
    with pytest.raises(ToolError, match="Unknown handle"):
        handles.resolve("ethiopia", h["handle"])
    with pytest.raises(ToolError, match="not an output handle"):
        handles.resolve("kenya", "../../etc/passwd")


def test_mint_copies_foreign_files_and_adopts_owned_ones(tmp_path):
    src = tmp_path / "elsewhere.nc"
    src.write_bytes(b"data")
    h = handles.mint("default", src, kind="dataset", label="copied")
    assert src.exists()  # copied, not moved: a library may still hold the original open
    assert handles.resolve("default", h["handle"]).read_bytes() == b"data"
    handle, path = handles.reserve("default", ".png")
    path.write_bytes(b"\x89PNG")
    assert handles.mint("default", path, kind="image", label="own")["handle"] == handle


def test_path_or_handle_passthrough():
    assert handles.path_or_handle("default", "/tmp/x.nc") == "/tmp/x.nc"


def test_tenant_modes(monkeypatch):
    assert tenant_from_headers(None) == "default"
    monkeypatch.setenv("S2S_TENANT_MODE", "header")
    with pytest.raises(ToolError, match="Missing x-s2s-tenant"):
        tenant_from_headers({})
    assert tenant_from_headers({"x-s2s-tenant": "kmd"}) == "kmd"
    with pytest.raises(ToolError, match="Invalid tenant"):
        tenant_from_headers({"x-s2s-tenant": "../x"})
    monkeypatch.setenv("S2S_TENANT_MODE", "bogus")
    with pytest.raises(ToolError, match="Unknown S2S_TENANT_MODE"):
        tenant_from_headers(None)
