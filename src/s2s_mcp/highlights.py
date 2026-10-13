"""Computed, deterministic highlights of a tercile forecast.

The widget shows these as three bullets and the model receives them as the
tool's text, so its "reading" of the outlook is grounded in numbers the
server computed rather than in the picture. Everything here is presentation
over africas2s' output: area-weighted shares of the dominant tercile, the
same per country, a one-word skill verdict, and one summary sentence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr

COUNTRIES_PATH = Path(__file__).resolve().parent / "web" / "africa-countries.geojson"
NO_SIGNAL_BELOW = 0.40          # max probability under this: "no clear signal"
COUNTRY_MIN_SHARE = 0.02        # countries covering less of the region are not listed
LABEL_MIN_SHARE = 0.10          # countries covering at least this much name the region
STRONGEST_MIN_SHARE = 0.05      # a country is a 'strongest' signal only above this dominant share
TERCILES = ("below", "normal", "above")
CLASSES = (*TERCILES, "none")
# (upper bound, word): rpss < 0 no skill, < 0.05 marginal, < 0.15 modest, else good
VERDICTS = ((0.0, "no skill"), (0.05, "marginal"), (0.15, "modest"), (float("inf"), "good"))


def skill_verdict(rpss: Any) -> str:
    try:
        value = float(rpss)
    except (TypeError, ValueError):
        return "unknown"
    if not np.isfinite(value):
        return "unknown"
    for upper, word in VERDICTS:
        if value < upper:
            return word
    return "good"


def _ring_area(ring: np.ndarray) -> float:
    x, y = ring[:, 0], ring[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def load_countries(path: Path = COUNTRIES_PATH) -> list[dict[str, Any]]:
    """Outer rings per country, smallest area first (so enclaves win)."""
    path = Path(path)
    if not path.is_file():
        return []
    fc = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for feature in fc.get("features", []):
        geom = feature.get("geometry") or {}
        polys = geom.get("coordinates", [])
        if geom.get("type") == "Polygon":
            polys = [polys]
        elif geom.get("type") != "MultiPolygon":
            continue
        rings = [np.asarray(p[0], dtype=float) for p in polys if p and len(p[0]) >= 4]
        if not rings:
            continue
        out.append({"name": str(feature.get("properties", {}).get("name", "?")),
                    "area": sum(_ring_area(r) for r in rings), "rings": rings})
    out.sort(key=lambda c: c["area"])
    return out


def assign_countries(lat: np.ndarray, lon: np.ndarray, countries: list[dict[str, Any]]) -> np.ndarray:
    """Country name per (lat, lon) cell centre, or None. Smallest country first."""
    from matplotlib.path import Path as MplPath

    LON, LAT = np.meshgrid(lon, lat)
    pts = np.column_stack([LON.ravel(), LAT.ravel()])
    names = np.full(pts.shape[0], None, dtype=object)
    free = np.ones(pts.shape[0], dtype=bool)
    for country in countries:
        hit = np.zeros(pts.shape[0], dtype=bool)
        for ring in country["rings"]:
            lo, hi = ring.min(axis=0), ring.max(axis=0)
            cand = free & (pts[:, 0] >= lo[0]) & (pts[:, 0] <= hi[0]) & (pts[:, 1] >= lo[1]) & (pts[:, 1] <= hi[1])
            if cand.any():
                hit[cand] |= MplPath(ring).contains_points(pts[cand])
        names[hit] = country["name"]
        free &= ~hit
        if not free.any():
            break
    return names.reshape(LAT.shape)


def _peak(values: np.ndarray) -> float:
    """Robust peak probability: the 95th percentile, so one degenerate cell cannot claim '100%'."""
    if values.size == 0:
        return 0.0
    return round(float(np.percentile(values, 95)), 3)


def _shares(weights: np.ndarray, classes: np.ndarray, mask: np.ndarray) -> dict[str, float]:
    w = np.where(mask, weights, 0.0)
    total = float(w.sum())
    if total <= 0:
        return {k: 0.0 for k in CLASSES}
    return {k: round(float(w[classes == i].sum() / total), 3) for i, k in enumerate(CLASSES)}


def _pct(x: float) -> str:
    return f"{round(100 * x):d}%"


def _summary(region: dict[str, float], strongest: dict[str, list], verdict: str, rpss: Any) -> str:
    def strongest_in(key: str) -> str:
        tops = strongest.get(key) or []
        if not tops:
            return ""
        names = " and ".join(t["name"] for t in tops)
        return f" (strongest in {names}, up to {_pct(tops[0]['max_prob'])})"

    parts = [
        f"Above-normal dominant over {_pct(region['above'])} of the region{strongest_in('above')}",
        f"near-normal over {_pct(region['normal'])}",
        f"below-normal over {_pct(region['below'])}{strongest_in('below')}",
        f"no clear signal over {_pct(region['none'])}",
    ]
    return ", ".join(parts) + f"; cross-validated skill {_skill_text(verdict, rpss)}."


def _skill_text(verdict: str, rpss: Any) -> str:
    try:
        return f"{verdict} (RPSS {float(rpss):.2f})"
    except (TypeError, ValueError):
        return verdict


def _empty(verdict: str) -> dict[str, Any]:
    return {"region_shares": {k: 0.0 for k in CLASSES}, "countries": [],
            "strongest": {"above": [], "below": []}, "skill_verdict": verdict, "region_label": ""}


def unavailable(skill: Any, reason: str) -> dict[str, Any]:
    """The highlights shape when they could not be computed (the recipe still returns its handles)."""
    verdict = skill_verdict(skill.get("rpss") if isinstance(skill, dict) else None)
    return {**_empty(verdict), "summary": f"Highlights unavailable ({reason}); cross-validated skill {verdict}."}


def compute(probs: xr.DataArray, skill: dict[str, Any], countries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Highlights for a (tercile, lat, lon) probability field (fractions 0-1)."""
    probs = probs.transpose("tercile", "lat", "lon")
    lat = probs["lat"].values.astype(float)
    lon = probs["lon"].values.astype(float)
    p = probs.values.astype(float)
    valid = np.isfinite(p).all(axis=0)
    weights = np.cos(np.deg2rad(lat))[:, None] * np.ones((1, lon.size))
    weights = np.where(valid, weights, 0.0)
    rpss = skill.get("rpss") if isinstance(skill, dict) else None
    verdict = skill_verdict(rpss)
    if not valid.any():
        return {**_empty(verdict), "summary": f"The forecast has no valid cells in this region; "
                                              f"cross-validated skill {_skill_text(verdict, rpss)}."}

    safe = np.where(valid[None], p, 0.0)
    dominant = safe.argmax(axis=0)
    pmax = safe.max(axis=0)
    classes = np.where(pmax < NO_SIGNAL_BELOW, 3, dominant)
    region_shares = _shares(weights, classes, valid)
    total = float(weights.sum())

    countries = load_countries() if countries is None else countries
    rows: list[dict[str, Any]] = []
    if countries:
        names = assign_countries(lat, lon, countries)
        for name in sorted({n for n in names.ravel() if n}):
            mask = (names == name) & valid
            share = float(weights[mask].sum() / total)
            if share < COUNTRY_MIN_SHARE:
                continue
            rows.append({
                "name": name,
                "share_of_region": round(share, 3),
                "dominant": _shares(weights, classes, mask),
                "max_prob": {"below": _peak(safe[0][mask]), "above": _peak(safe[2][mask])},
            })
        rows.sort(key=lambda r: -r["share_of_region"])

    def top(key: str) -> list[dict[str, Any]]:
        # A country counts as a signal only when a meaningful share of it is dominated by that
        # tercile and the peak probability clears the "no clear signal" edge.
        ranked = sorted((r for r in rows
                         if r["dominant"][key] >= STRONGEST_MIN_SHARE and r["max_prob"][key] >= NO_SIGNAL_BELOW),
                        key=lambda r: -r["dominant"][key])[:2]
        return [{"name": r["name"], "max_prob": r["max_prob"][key], "share": r["dominant"][key]} for r in ranked]

    strongest = {"above": top("above"), "below": top("below")}
    label = ", ".join(r["name"] for r in rows[:3] if r["share_of_region"] >= LABEL_MIN_SHARE)
    return {
        "region_shares": region_shares,
        "countries": rows,
        "strongest": strongest,
        "skill_verdict": verdict,
        "region_label": label,
        "summary": _summary(region_shares, strongest, verdict, rpss),
    }
