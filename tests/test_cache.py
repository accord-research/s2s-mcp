from s2s_mcp import cache, handles
from s2s_mcp.tenancy import tenant_dir


def _mint(tenant="default"):
    handle, path = handles.reserve(tenant, ".nc")
    path.write_bytes(b"x")
    handles.register(tenant, handle, kind="dataset", label="t")
    return handle


def test_key_is_stable_and_order_independent():
    a = cache.key({"init": "2025-02", "region": [1, 2, 3, 4], "title": None})
    b = cache.key({"title": None, "region": [1, 2, 3, 4], "init": "2025-02"})
    assert a == b and len(a) == 24
    assert cache.key({"init": "2025-03"}) != a


def test_miss_then_hit_and_tenant_isolation():
    args = {"init": "2025-02", "method": "cca"}
    assert cache.get("default", args) is None
    result = {"tercile_forecast": _mint(), "skill": {"rpss": 0.1}}
    cache.put("default", args, result)
    assert cache.get("default", args) == result
    assert cache.get("other", args) is None
    assert (tenant_dir("default") / "cache").is_dir()


def test_cache_misses_when_handle_missing():
    args = {"init": "2025-02"}
    h = _mint()
    cache.put("default", args, {"tercile_forecast": h})
    for p in tenant_dir("default").glob(f"{h}.*"):
        p.unlink()
    assert cache.get("default", args) is None
    assert not list((tenant_dir("default") / "cache").glob("*.json"))   # stale entry removed


def test_corrupt_entry_is_a_miss():
    args = {"x": 1}
    cache.put("default", args, {"a": 1})
    path = next((tenant_dir("default") / "cache").glob("*.json"))
    path.write_text("{not json")
    assert cache.get("default", args) is None


def test_disabled_by_env(monkeypatch):
    monkeypatch.setenv("S2S_RECIPE_CACHE", "0")
    cache.put("default", {"x": 1}, {"a": 1})
    assert cache.get("default", {"x": 1}) is None
    assert not (tenant_dir("default") / "cache").exists()


def test_key_includes_format_version(monkeypatch):
    a = cache.key({"x": 1})
    monkeypatch.setattr(cache, "CACHE_VERSION", "test-bump")
    assert cache.key({"x": 1}) != a
