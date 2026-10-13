import type { Layer, Style } from "./palette";
import type { Bounds } from "./geometry";

export type Highlights = {
  region_shares: { below: number; normal: number; above: number; none: number };
  countries: { name: string; share_of_region: number; dominant: Record<string, number>; max_prob: { below: number; above: number } }[];
  strongest: { above: { name: string; max_prob: number; share: number }[]; below: { name: string; max_prob: number; share: number }[] };
  skill_verdict: string;
  region_label: string;
  summary: string;
};
export type OutlookRequest = { init: string; target: string; region: number[] | string; models: string[]; predictand: string;
                               variable: string; hindcast: number[]; method: string; cv_window: number; title: string | null };
export type OutlookResult = {
  status?: "done";
  models: string[];
  skill: Record<string, number | null>;
  metadata: Record<string, unknown>;
  tercile_forecast: string;
  tercile_map?: string;
  bounds: Bounds;
  request: OutlookRequest;
  highlights: Highlights;
  summary: string;
  palette?: string;
  map_error?: string;
};
/** The server's answer while a cold run is still going: the widget polls until it is done. */
export type RunningResult = { status: "running"; request: OutlookRequest; elapsed_seconds: number; poll_after_seconds: number; message: string };
export type Pending = { request: OutlookRequest; elapsed: number };
export type GridPayload = {
  handle: string; bounds: Bounds; lat: number[]; lon: number[]; factor: number;
  below: (number | null)[]; normal: (number | null)[]; above: (number | null)[]; style: Style;
};
export type Cursor = { lat: number; lon: number; country: string | null; b: number | null; n: number | null; a: number | null };
export type State = {
  result: OutlookResult | null;
  grid: GridPayload | null;
  layer: Layer;
  mode: "inline" | "fullscreen";
  fullscreenAvailable: boolean;
  busy: boolean;
  pending: Pending | null;        // a run still computing on the server (initial or a method switch)
  error: string | null;
  cursor: Cursor | null;
  panelHidden: boolean;
};
export const METHODS: [string, string][] = [["cca", "CCA"], ["bcsd", "BCSD"], ["climatology", "Climatology"]];
