import json

import numpy as np
import pytest
import xarray as xr

from s2s_mcp import highlights


def square(name, lon0, lon1, lat0, lat1):
    ring = [[lon0, lat0], [lon1, lat0], [lon1, lat1], [lon0, lat1], [lon0, lat0]]
    return {"type": "Feature", "properties": {"name": name},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


@pytest.fixture
def countries(tmp_path):
    # West: lon 30-35 ; East: lon 35-40 ; Enclave inside East: lon 37-38, lat -3..-1
    fc = {"type": "FeatureCollection", "features": [
        square("West", 30, 35, -10, 4), square("East", 35, 40, -10, 4), square("Enclave", 37, 38, -3, -1)]}
    p = tmp_path / "countries.geojson"
    p.write_text(json.dumps(fc))
    return highlights.load_countries(p)


def field(fill):
    """(tercile, lat, lon) on a 14x20 grid, lat -9.5..3.5, lon 30.25..39.75; fill(lat, lon) -> (b, n, a)."""
    lat = np.arange(-9.5, 4, 1.0)
    lon = np.arange(30.25, 40, 0.5)
    p = np.zeros((3, lat.size, lon.size))
    for i, la in enumerate(lat):
        for j, lo in enumerate(lon):
            p[:, i, j] = fill(la, lo)
    return xr.DataArray(p, dims=("tercile", "lat", "lon"), coords={"tercile": [0, 1, 2], "lat": lat, "lon": lon})


def test_load_countries_sorted_smallest_first(countries):
    assert [c["name"] for c in countries] == ["Enclave", "West", "East"]
    assert countries[0]["rings"][0].shape == (5, 2)


def test_region_shares_sum_to_one_and_split(countries):
    # West half wet (above 0.55), East half dry (below 0.50), enclave no signal (max 0.36)
    def fill(la, lo):
        if 37 <= lo < 38 and -3 <= la < -1:
            return (0.32, 0.36, 0.32)
        return (0.20, 0.25, 0.55) if lo < 35 else (0.50, 0.30, 0.20)
    out = highlights.compute(field(fill), {"rpss": 0.12}, countries)
    rs = out["region_shares"]
    assert abs(sum(rs.values()) - 1.0) < 1e-6
    assert rs["above"] == pytest.approx(0.5, abs=0.02)
    assert rs["none"] > 0 and rs["normal"] == 0.0
    names = {c["name"]: c for c in out["countries"]}
    assert names["West"]["dominant"]["above"] == pytest.approx(1.0)
    assert names["East"]["dominant"]["below"] > 0.9
    # The enclave is < 2% of the region so it is not tabulated; check its precedence directly.
    da = field(fill)
    assigned = highlights.assign_countries(da["lat"].values, da["lon"].values, countries)
    assert assigned[7, 14] == "Enclave" and assigned[7, 10] == "East"   # enclave wins over East
    assert names["West"]["max_prob"]["above"] == pytest.approx(0.55)
    assert out["strongest"]["above"][0]["name"] == "West"
    assert out["strongest"]["below"][0]["name"] == "East"
    assert out["skill_verdict"] == "modest"
    assert out["region_label"] == "West, East"          # Enclave < 10% of the region
    assert "Above-normal dominant over" in out["summary"] and "RPSS 0.12" in out["summary"]
    json.dumps(out)


def test_small_countries_are_dropped(countries):
    out = highlights.compute(field(lambda la, lo: (0.2, 0.3, 0.5)), {"rpss": 0.2}, countries)
    # Enclave covers 4 of 280 cells (<2%) -> dropped from the table
    assert {c["name"] for c in out["countries"]} == {"West", "East"}


@pytest.mark.parametrize("rpss,word", [(-0.1, "no skill"), (0.0, "marginal"), (0.04, "marginal"),
                                       (0.05, "modest"), (0.149, "modest"), (0.2, "good"), (None, "unknown")])
def test_skill_verdict(rpss, word):
    assert highlights.skill_verdict(rpss) == word


def test_highlights_all_nan(countries):
    out = highlights.compute(field(lambda la, lo: (np.nan, np.nan, np.nan)), {"rpss": 0.1}, countries)
    assert out["region_shares"] == {"below": 0.0, "normal": 0.0, "above": 0.0, "none": 0.0}
    assert out["countries"] == [] and out["strongest"] == {"above": [], "below": []}
    assert out["region_label"] == ""
    assert "no valid cells" in out["summary"]


def test_without_countries_region_only():
    out = highlights.compute(field(lambda la, lo: (0.2, 0.3, 0.5)), {"rpss": 0.1}, countries=[])
    assert out["countries"] == [] and out["region_label"] == ""
    assert out["region_shares"]["above"] == pytest.approx(1.0)


def test_missing_geojson_is_empty(tmp_path):
    assert highlights.load_countries(tmp_path / "nope.geojson") == []


def test_summary_names_all_three_terciles(countries):
    out = highlights.compute(field(lambda la, lo: (0.2, 0.5, 0.3)), {"rpss": 0.12}, countries)
    assert out["region_shares"]["normal"] == pytest.approx(1.0)
    assert "near-normal over 100%" in out["summary"]
    assert "Above-normal dominant over 0%" in out["summary"] and "below-normal over 0%" in out["summary"]


def test_shipped_countries_use_neutral_names():
    names = {c["name"] for c in highlights.load_countries()}
    assert {"Somalia", "South Sudan", "DR Congo", "Western Sahara", "Equatorial Guinea",
            "Central African Republic", "Eswatini"} <= names
    assert not {"Somaliland", "S. Sudan", "Dem. Rep. Congo", "W. Sahara", "Eq. Guinea", "Central African Rep.",
                "eSwatini", "Fiji", "Siachen Glacier", "N. Cyprus", "Kosovo", "Indian Ocean Ter."} & names


def test_unavailable_has_the_full_shape():
    out = highlights.unavailable({"rpss": 0.2}, "bad geojson")
    assert out["region_shares"] == {"below": 0.0, "normal": 0.0, "above": 0.0, "none": 0.0}
    assert out["countries"] == [] and out["strongest"] == {"above": [], "below": []}
    assert out["skill_verdict"] == "good" and out["region_label"] == ""
    assert "unavailable" in out["summary"] and "bad geojson" in out["summary"]


def test_max_prob_ignores_a_single_degenerate_cell(countries):
    def fill(la, lo):
        return (0.2, 0.3, 1.0) if (la, lo) == (-9.5, 30.25) else (0.2, 0.3, 0.5)
    out = highlights.compute(field(fill), {"rpss": 0.1}, countries)
    west = next(c for c in out["countries"] if c["name"] == "West")
    assert west["max_prob"]["above"] == pytest.approx(0.5)
    assert "up to 50%" in out["summary"]


def test_strongest_ignores_negligible_signals(countries):
    # West: wet everywhere; East: wet except two cells barely dry -> no "strongest dry" entry
    def fill(la, lo):
        if lo >= 35 and la < -8.5 and lo < 36:
            return (0.42, 0.30, 0.28)
        return (0.20, 0.25, 0.55)
    out = highlights.compute(field(fill), {"rpss": 0.1}, countries)
    assert out["strongest"]["below"] == []
    assert "strongest in" in out["summary"] and "below-normal over" in out["summary"]
    assert "up to 0%" not in out["summary"]
