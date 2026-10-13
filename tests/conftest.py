import asyncio

import numpy as np
import pytest
import xarray as xr


@pytest.fixture(autouse=True)
def _isolated_outputs(tmp_path, monkeypatch):
    """Every test gets its own output root and single-tenant mode."""
    monkeypatch.setenv("S2S_OUTPUT_ROOT", str(tmp_path / "outputs"))
    monkeypatch.setenv("S2S_TENANT_MODE", "single")
    monkeypatch.delenv("S2S_PUBLIC_BASE_URL", raising=False)
    # The primitives' own workdirs, for anything that does not pass destination.
    monkeypatch.setenv("ACMADDL_MCP_WORKDIR", str(tmp_path / "acmaddl"))
    monkeypatch.setenv("AFRICAS2S_MCP_WORKDIR", str(tmp_path / "africas2s"))
    return tmp_path


def run(coro):
    return asyncio.run(coro)


YEARS = np.arange(1993, 2016)
MEMBERS = np.arange(4)
CLAT, CLON = np.linspace(-10, 4, 6), np.linspace(30, 40, 6)
FLAT, FLON = np.linspace(-10, 4, 12), np.linspace(30, 40, 12)


def synthetic_gcm(seed: int = 1) -> xr.DataArray:
    rng = np.random.default_rng(seed)
    signal = np.sin(np.arange(len(YEARS)) * 0.4)
    spatial = np.outer(np.sin(CLAT * 0.5), np.cos(CLON * 0.3))
    data = (signal[:, None, None, None] * spatial[None, None] * 20
            + 100 + rng.normal(0, 5, (len(YEARS), len(MEMBERS), len(CLAT), len(CLON))))
    return xr.DataArray(data, dims=("year", "member", "lat", "lon"),
                        coords={"year": YEARS, "member": MEMBERS, "lat": CLAT, "lon": CLON},
                        name="precip", attrs={"units": "mm"})


def synthetic_obs(seed: int = 2) -> xr.DataArray:
    rng = np.random.default_rng(seed)
    signal = np.sin(np.arange(len(YEARS)) * 0.4)
    spatial = np.outer(np.sin(FLAT * 0.5), np.cos(FLON * 0.3))
    data = signal[:, None, None] * spatial[None] * 20 + 100 + rng.normal(0, 8, (len(YEARS), len(FLAT), len(FLON)))
    return xr.DataArray(data, dims=("year", "lat", "lon"),
                        coords={"year": YEARS, "lat": FLAT, "lon": FLON},
                        name="precip", attrs={"units": "mm"})


@pytest.fixture
def gcm():
    return synthetic_gcm()


@pytest.fixture
def obs():
    return synthetic_obs()


@pytest.fixture
def fake_acmaddl(monkeypatch, gcm, obs):
    """Stand in for acmaddl's network verbs with synthetic data."""
    import acmaddl

    calls = {"fetch": [], "assemble": []}

    def _fetch(product, variable, **kwargs):
        kwargs = {"product": product, "variable": variable, **kwargs}
        calls["fetch"].append(kwargs)
        da = obs if product.startswith("obs/") else gcm
        ds = da.to_dataset(name="precip")
        if kwargs.get("destination"):
            ds.to_netcdf(kwargs["destination"])
        return ds

    def _assemble(roster, variable, **kwargs):
        calls["assemble"].append({"roster": roster, "variable": variable, **kwargs})
        return {label: (gcm, gcm.isel(year=-1, drop=True).expand_dims(year=[int(YEARS[-1]) + 9]))
                for label, _product, _window in roster}

    monkeypatch.setattr(acmaddl, "fetch", _fetch)
    monkeypatch.setattr(acmaddl, "assemble", _assemble)
    return calls


@pytest.fixture(autouse=True)
def _fresh_jobs():
    """Background jobs are process-global; give every test its own registry."""
    from s2s_mcp import jobs

    jobs.registry = jobs.Registry()
    yield
