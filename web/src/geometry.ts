// Pure layout maths: letterboxing at the region's plate carrée aspect, zoom
// and pan clamping, and pixel-to-cell lookup. No DOM here so it is unit
// testable.
export type Bounds = [latS: number, latN: number, lonW: number, lonE: number];

/** Width / height of the region in degrees (plate carrée: 1° lon = 1° lat on screen). */
export function aspectOf(b: Bounds): number {
  return (b[3] - b[2]) / (b[1] - b[0]);
}

/** Largest box with `aspect` fitting inside areaW x areaH. */
/** Inline cards cap the map height: wide hosts (ChatGPT's column is ~760px) would
 *  otherwise get a map taller than the viewport, pushing the chips and highlights
 *  below the fold. Fullscreen is the place for the big map. */
export const INLINE_MAX_MAP_HEIGHT = 440;

export function inlineMapArea(cardWidth: number): { w: number; h: number } {
  const w = Math.max(120, cardWidth - 28);
  return { w, h: Math.min(Math.round(w * 0.9), INLINE_MAX_MAP_HEIGHT) };
}

export function fitBox(areaW: number, areaH: number, aspect: number): { w: number; h: number } {
  const w = Math.min(areaW, areaH * aspect);
  return { w, h: w / aspect };
}

export function clampTransform(s: number, tx: number, ty: number, W: number, H: number) {
  const sc = Math.max(1, Math.min(8, s));
  return { s: sc, tx: Math.min(0, Math.max(W - W * sc, tx)), ty: Math.min(0, Math.max(H - H * sc, ty)) };
}

/** Zoom by `factor` keeping the point (mx, my) fixed; clamps scale to [1, maxS]. */
export function zoomAt(s: number, tx: number, ty: number, factor: number, mx: number, my: number, W: number, H: number, maxS = 8) {
  const ns = Math.max(1, Math.min(maxS, s * factor));
  return clampTransform(ns, mx - (mx - tx) * (ns / s), my - (my - ty) * (ns / s), W, H);
}

/** Grid cell under pixel (x, y) of a W x H map box, or null when outside. Row 0 is the southernmost. */
export function cellAt(x: number, y: number, W: number, H: number, bounds: Bounds, nlat: number, nlon: number) {
  if (x < 0 || y < 0 || x >= W || y >= H) return null;
  const [latS, latN, lonW, lonE] = bounds;
  const lon = lonW + (x / W) * (lonE - lonW);
  const lat = latN - (y / H) * (latN - latS);
  const i = Math.min(nlat - 1, Math.floor(((lat - latS) / (latN - latS)) * nlat));
  const j = Math.min(nlon - 1, Math.floor(((lon - lonW) / (lonE - lonW)) * nlon));
  return { i, j, lat, lon };
}

export function fullscreenAvailable(modes: string[] | undefined): boolean {
  return Array.isArray(modes) && modes.includes("fullscreen");
}
