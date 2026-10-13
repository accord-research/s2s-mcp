// Colour rules for the tercile map. The palette itself (colours and bin
// edges) comes from the server's `style` payload, serialised from africas2s'
// named TercileStyle, so the widget matches the server's PNG maps exactly.
export type Style = {
  below_colors: string[];
  normal_colors: string[];
  above_colors: string[];
  prob_bins: number[]; // percent, e.g. [33.33, 36, 40, 50, 60, 70, 100.01]
  dry_color: string;
  nodata_color: string;
};
export type Layer = "dominant" | "below" | "normal" | "above";
export const LIGHT_NO_SIGNAL = "#f4f4f4";
export const TERCILE_NAMES = ["below", "normal", "above"] as const;

/** Index of the band containing `pct`, or -1 when below the first edge. */
export function bandIndex(pct: number, bins: number[]): number {
  if (pct < bins[0]) return -1;
  for (let k = 1; k < bins.length; k++) if (pct < bins[k]) return k - 1;
  return bins.length - 2;
}

export function classify(b: number, n: number, a: number): { tercile: 0 | 1 | 2; p: number } {
  if (a >= b && a >= n) return { tercile: 2, p: a };
  if (b >= n) return { tercile: 0, p: b };
  return { tercile: 1, p: n };
}

function ramp(style: Style, tercile: 0 | 1 | 2): string[] {
  return tercile === 0 ? style.below_colors : tercile === 1 ? style.normal_colors : style.above_colors;
}

/** Colour for one cell, or null (transparent) when the cell is NaN. */
export function cellColor(b: number | null, n: number | null, a: number | null, style: Style, layer: Layer): string | null {
  if (b == null || n == null || a == null || Number.isNaN(b) || Number.isNaN(n) || Number.isNaN(a)) return null;
  if (layer === "dominant") {
    const { tercile, p } = classify(b, n, a);
    const band = bandIndex(100 * p, style.prob_bins);
    return band < 0 ? style.nodata_color : ramp(style, tercile)[Math.min(band, 5)];
  }
  const tercile = layer === "below" ? 0 : layer === "normal" ? 1 : 2;
  const p = tercile === 0 ? b : tercile === 1 ? n : a;
  const band = bandIndex(100 * p, style.prob_bins);
  return band < 0 ? LIGHT_NO_SIGNAL : ramp(style, tercile)[Math.min(band, 5)];
}
