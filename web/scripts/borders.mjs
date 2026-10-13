// Clip Natural Earth 50m countries (world-atlas) to the Africa window and
// write (a) a pruned TopoJSON for the widget bundle and (b) a GeoJSON of outer
// rings for the server's per-country highlights.
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import * as topojson from "topojson-client";

const require = createRequire(import.meta.url);
const here = dirname(fileURLToPath(import.meta.url));
const WINDOW = { lonW: -25, lonE: 60, latS: -40, latN: 40 };

const topo = JSON.parse(readFileSync(require.resolve("world-atlas/countries-50m.json"), "utf8"));
const fc = topojson.feature(topo, topo.objects.countries);

function bbox(coords, box = [Infinity, Infinity, -Infinity, -Infinity]) {
  if (typeof coords[0] === "number") {
    box[0] = Math.min(box[0], coords[0]); box[1] = Math.min(box[1], coords[1]);
    box[2] = Math.max(box[2], coords[0]); box[3] = Math.max(box[3], coords[1]);
  } else for (const c of coords) bbox(c, box);
  return box;
}
// Keep a country if any one of its polygons intersects the window (a whole
// multipolygon bbox would keep the USA via Alaska's antimeridian wrap).
const intersects = (ring) => {
  const [x0, y0, x1, y1] = bbox(ring);
  return x1 >= WINDOW.lonW && x0 <= WINDOW.lonE && y1 >= WINDOW.latS && y0 <= WINDOW.latN;
};
// Naming policy: neutral names for an ACMAD / AU audience. Natural Earth's de
// facto layer carries units and abbreviations we do not want in a summary
// sentence; Somaliland is folded into Somalia by name (the server groups
// cells by name), and units outside the region of interest are dropped.
const RENAMES = {
  "Somaliland": "Somalia", "W. Sahara": "Western Sahara", "Dem. Rep. Congo": "DR Congo",
  "S. Sudan": "South Sudan", "Eq. Guinea": "Equatorial Guinea",
  "Central African Rep.": "Central African Republic", "eSwatini": "Eswatini",
};
const DROP = new Set(["Fiji", "Siachen Glacier", "N. Cyprus", "Kosovo", "Indian Ocean Ter."]);
const nameOf = (props) => RENAMES[props.name] ?? props.name;

// Keyed by index, not id: Natural Earth gives every disputed unit the same id (-99).
const keep = new Set();
fc.features.forEach((f, i) => {
  if (DROP.has(f.properties.name)) return;
  const polys = f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.coordinates;
  if (polys.some((p) => intersects(p[0]))) keep.add(i);
});

// (a) pruned topology: only kept geometries and the arcs they reference
const geometries = topo.objects.countries.geometries.filter((_g, i) => keep.has(i));
const used = new Set();
const walk = (arcs) => { for (const a of arcs) typeof a === "number" ? used.add(a < 0 ? ~a : a) : walk(a); };
for (const g of geometries) walk(g.arcs);
const order = [...used].sort((a, b) => a - b);
const remap = new Map(order.map((old, i) => [old, i]));
const rewrite = (arcs) => arcs.map((a) => (typeof a === "number" ? (a < 0 ? ~remap.get(~a) : remap.get(a)) : rewrite(a)));
const pruned = {
  type: "Topology", transform: topo.transform, arcs: order.map((i) => topo.arcs[i]),
  objects: { countries: { type: "GeometryCollection",
    geometries: geometries.map((g) => ({ type: g.type, arcs: rewrite(g.arcs), properties: { name: nameOf(g.properties) } })) } },
};
mkdirSync(resolve(here, "../data"), { recursive: true });
writeFileSync(resolve(here, "../data/africa-50m.topo.json"), JSON.stringify(pruned));

// (b) GeoJSON for the server (rounded, outer rings only)
const round = (c) => (typeof c[0] === "number" ? [Math.round(c[0] * 1000) / 1000, Math.round(c[1] * 1000) / 1000] : c.map(round));
const features = fc.features.filter((_f, i) => keep.has(i)).map((f) => ({
  type: "Feature", properties: { name: nameOf(f.properties) },
  geometry: f.geometry.type === "Polygon"
    ? { type: "Polygon", coordinates: [round(f.geometry.coordinates[0])] }
    : { type: "MultiPolygon", coordinates: f.geometry.coordinates.map((p) => [round(p[0])]) },
}));
mkdirSync(resolve(here, "../../src/s2s_mcp/web"), { recursive: true });
writeFileSync(resolve(here, "../../src/s2s_mcp/web/africa-countries.geojson"),
  JSON.stringify({ type: "FeatureCollection", features }));
console.log(`borders: ${features.length} countries, ${order.length} arcs`);
