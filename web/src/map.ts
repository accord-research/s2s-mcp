// The map: one canvas of probability cells, one canvas of Natural Earth
// borders, both inside a transformed wrapper for zoom/pan. Plate carrée: the
// map box is letterboxed to the region's degree aspect and cells are drawn as
// rectangles in lon/lat space, so the picture matches the server's PNGs.
import { geoBounds, geoContains, geoEquirectangular, geoPath } from "d3-geo";
import * as topojson from "topojson-client";
import type { GeometryCollection, Topology } from "topojson-specification";
import type { Feature, FeatureCollection, Geometry, MultiLineString } from "geojson";
import topoData from "../data/africa-50m.topo.json";
import { cellAt, clampTransform, fitBox, zoomAt, type Bounds } from "./geometry";
import { cellColor, type Layer } from "./palette";
import type { GridPayload } from "./types";

type CountryProps = { name: string };
const topo = topoData as unknown as Topology<{ countries: GeometryCollection<CountryProps> }>;
const countries = topojson.feature(topo, topo.objects.countries) as FeatureCollection<Geometry, CountryProps>;
const countryBoxes = countries.features.map((f) => geoBounds(f));
const borders: MultiLineString = topojson.mesh(topo, topo.objects.countries, (a, b) => a !== b);
const coast: MultiLineString = topojson.mesh(topo, topo.objects.countries, (a, b) => a === b);

export type Cell = { i: number; j: number; lat: number; lon: number };

export class OutlookMap {
  readonly el: HTMLDivElement;
  private inner: HTMLDivElement;
  private cells: HTMLCanvasElement;
  private lines: HTMLCanvasElement;
  private bounds: Bounds | null = null;
  private grid: GridPayload | null = null;
  private layer: Layer = "dominant";
  private W = 0;
  private H = 0;
  private s = 1;
  private tx = 0;
  private ty = 0;
  private drag: { x: number; y: number; tx: number; ty: number; moved: boolean } | null = null;
  private zoomable = false;
  onHover: (cell: Cell | null) => void = () => {};

  constructor(container: HTMLElement) {
    this.el = document.createElement("div");
    this.el.className = "map";
    this.inner = document.createElement("div");
    this.inner.className = "inner";
    this.cells = document.createElement("canvas");
    this.lines = document.createElement("canvas");
    this.inner.append(this.cells, this.lines);
    this.el.append(this.inner);
    container.append(this.el);
    this.el.addEventListener("pointermove", (e) => this.pointer(e));
    this.el.addEventListener("pointerleave", () => this.onHover(null));
    this.el.addEventListener("wheel", (e) => {
      if (!this.zoomable) return;            // inline: let the conversation scroll
      e.preventDefault();
      const r = this.el.getBoundingClientRect();
      this.apply(zoomAt(this.s, this.tx, this.ty, e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX - r.left, e.clientY - r.top, this.W, this.H));
    }, { passive: false });
    this.el.addEventListener("pointerdown", (e) => {
      if (!this.zoomable) return;
      this.drag = { x: e.clientX, y: e.clientY, tx: this.tx, ty: this.ty, moved: false };
      this.el.setPointerCapture(e.pointerId);
    });
    const end = () => { this.drag = null; this.el.classList.remove("grabbing"); };
    this.el.addEventListener("pointerup", end);
    this.el.addEventListener("pointercancel", end);
  }

  setBounds(b: Bounds) { this.bounds = b; this.draw(); }
  setData(grid: GridPayload, layer: Layer) {
    const moved = !this.bounds || this.bounds.some((v, i) => v !== grid.bounds[i]);
    this.grid = grid; this.layer = layer; this.bounds = grid.bounds;
    if (moved) { this.s = 1; this.tx = 0; this.ty = 0; }   // a new region starts unzoomed
    this.apply({ s: this.s, tx: this.tx, ty: this.ty });
    this.draw();
  }
  /** Zoom and pan are fullscreen affordances; inline the card must not steal the page's scroll. */
  setZoomable(on: boolean) {
    this.zoomable = on;
    this.el.style.touchAction = on ? "none" : "auto";
    this.el.style.cursor = on ? "grab" : "crosshair";
    if (!on) this.reset();
  }
  setLayer(layer: Layer) { this.layer = layer; this.draw(); }

  /** Size the map box to the region aspect inside areaW x areaH (uniform, never stretched). */
  fit(areaW: number, areaH: number) {
    if (!this.bounds || areaW <= 0 || areaH <= 0) return;
    const aspect = (this.bounds[3] - this.bounds[2]) / (this.bounds[1] - this.bounds[0]);
    const { w, h } = fitBox(areaW, areaH, aspect);
    this.W = Math.max(1, Math.floor(w));
    this.H = Math.max(1, Math.floor(h));
    this.el.style.width = `${this.W}px`;
    this.el.style.height = `${this.H}px`;
    this.apply({ s: this.s, tx: this.tx, ty: this.ty });
    this.draw();
  }
  zoomBy(factor: number) { this.apply(zoomAt(this.s, this.tx, this.ty, factor, this.W / 2, this.H / 2, this.W, this.H)); }
  reset() { this.apply({ s: 1, tx: 0, ty: 0 }); }
  get zoom() { return this.s; }

  countryAt(lon: number, lat: number): string | null {
    for (let k = 0; k < countries.features.length; k++) {
      const [[x0, y0], [x1, y1]] = countryBoxes[k];
      if (lon < x0 || lon > x1 || lat < y0 || lat > y1) continue;
      const f: Feature<Geometry, CountryProps> = countries.features[k];
      if (geoContains(f, [lon, lat])) return f.properties.name;
    }
    return null;
  }

  private apply(t: { s: number; tx: number; ty: number }) {
    const c = clampTransform(t.s, t.tx, t.ty, this.W, this.H);
    this.s = c.s; this.tx = c.tx; this.ty = c.ty;
    this.inner.style.transform = `translate(${this.tx}px, ${this.ty}px) scale(${this.s})`;
  }

  private pointer(e: PointerEvent) {
    if (this.drag) {
      const dx = e.clientX - this.drag.x, dy = e.clientY - this.drag.y;
      if (!this.drag.moved && Math.hypot(dx, dy) > 3) { this.drag.moved = true; this.el.classList.add("grabbing"); }
      if (this.drag.moved) { this.apply({ s: this.s, tx: this.drag.tx + dx, ty: this.drag.ty + dy }); return; }
    }
    if (!this.grid || !this.bounds) return;
    const r = this.el.getBoundingClientRect();
    // undo zoom/pan to get map-box coordinates
    const x = (e.clientX - r.left - this.tx) / this.s;
    const y = (e.clientY - r.top - this.ty) / this.s;
    this.onHover(cellAt(x, y, this.W, this.H, this.bounds, this.grid.lat.length, this.grid.lon.length));
  }

  private draw() {
    if (!this.bounds || this.W === 0) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    for (const c of [this.cells, this.lines]) { c.width = this.W * dpr; c.height = this.H * dpr; }
    const g = this.cells.getContext("2d")!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, this.W, this.H);
    if (this.grid) {
      const { lat, lon, below, normal, above, style } = this.grid;
      const nlat = lat.length, nlon = lon.length, cw = this.W / nlon, ch = this.H / nlat;
      for (let i = 0; i < nlat; i++) {
        for (let j = 0; j < nlon; j++) {
          const k = i * nlon + j;
          const color = cellColor(below[k], normal[k], above[k], style, this.layer);
          if (!color) continue;
          g.fillStyle = color;
          g.fillRect(j * cw, this.H - (i + 1) * ch, cw + 0.5, ch + 0.5);
        }
      }
    }
    const [latS, latN, lonW, lonE] = this.bounds;
    // Explicit plate carrée: the box is already letterboxed to the region's aspect, so one
    // scale serves both axes. (fitExtent on a bbox polygon is winding-sensitive in d3-geo.)
    const projection = geoEquirectangular()
      .scale(this.W / (((lonE - lonW) * Math.PI) / 180))
      .center([(lonW + lonE) / 2, (latS + latN) / 2])
      .translate([this.W / 2, this.H / 2]);
    const l = this.lines.getContext("2d")!;
    l.setTransform(dpr, 0, 0, dpr, 0, 0);
    l.clearRect(0, 0, this.W, this.H);
    const path = geoPath(projection, l);
    l.lineWidth = 0.8; l.strokeStyle = "rgba(40,40,40,0.9)"; l.beginPath(); path(borders); l.stroke();
    l.lineWidth = 1.1; l.strokeStyle = "rgba(30,60,90,0.95)"; l.beginPath(); path(coast); l.stroke();
  }
}
