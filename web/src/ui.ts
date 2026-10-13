// DOM for the two layouts (inline card, fullscreen with a reading panel).
// Pure "state -> DOM": render() rebuilds the tree; the map object is handed
// back through `holder` so main.ts can feed it data and read hovers.
import { inlineMapArea, type Bounds } from "./geometry";
import { OutlookMap, type Cell } from "./map";
import type { Layer } from "./palette";
import { pendingMessage } from "./pending";
import { METHODS, type Cursor, type OutlookRequest, type State } from "./types";

export type Actions = {
  setMethod(m: string): void;
  setLayer(l: Layer): void;
  explain(): void;
  expand(): void;
  collapsePanel(hidden: boolean): void;
  retry(): void;
  hover(cell: Cell | null): void;
};
export type MapHolder = { map: OutlookMap | null };

export const pct = (x: number | null | undefined) => (x == null || Number.isNaN(x) ? "–" : `${Math.round(100 * x)}%`);
const num = (x: number | null | undefined, d = 2) => (x == null || Number.isNaN(x) ? "–" : x.toFixed(d));
const el = <K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string, text?: string): HTMLElementTagNameMap[K] => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};
const LAYERS: [Layer, string][] = [["dominant", "Dominant"], ["below", "Below"], ["normal", "Normal"], ["above", "Above"]];

export function methodLabel(key: string): string {
  return METHODS.find(([k]) => k === key)?.[1] ?? key;
}

const modelLabel = (id: string) => id.replace(/^[^/]+\//, "");

export function titleOf(s: State): { title: string; sub: string } {
  const r = s.result;
  const q: OutlookRequest = r ? r.request : s.pending!.request;
  const year = q.init.slice(0, 4);
  const bounds = r ? r.bounds : Array.isArray(q.region) ? q.region : null;
  const label = r?.highlights?.region_label || (bounds ? `[${bounds.map((v) => v.toFixed(1)).join(", ")}]` : String(q.region));
  const variable = q.variable === "temp" ? "temperature" : "rainfall";
  const models = r ? r.models : q.models.map(modelLabel);
  return {
    title: `${q.target} ${year} ${variable} outlook · ${label}`,
    sub: `${models.join(" + ")} · ${methodLabel(q.method)} onto ${q.predictand.replace(/^obs\//, "")}`,
  };
}

export function cursorText(c: Cursor | null): string {
  if (!c) return "";
  return `${c.lat.toFixed(1)}°, ${c.lon.toFixed(1)}°${c.country ? ` · ${c.country}` : ""} — below ${pct(c.b)} · normal ${pct(c.n)} · above ${pct(c.a)}`;
}

function ramp(colors: string[]) {
  const r = el("span", "ramp");
  for (const c of colors) { const i = el("i"); i.style.background = c; r.append(i); }
  return r;
}

function legend(s: State) {
  const st = s.grid?.style;
  const box = el("div", "legend");
  if (!st) return box;
  for (const [name, colors] of [["Below", st.below_colors], ["Normal", st.normal_colors], ["Above", st.above_colors]] as const) {
    box.append(el("span", undefined, name), ramp(colors));
  }
  box.append(el("span", undefined, `· ${st.prob_bins.slice(0, -1).map((b) => Math.round(b)).join(" · ")} %`));
  return box;
}

function roc(k: Record<string, number | null>, which: "bn" | "an") {
  return num(k[`roc_${which}`] ?? k[which === "bn" ? "roc_area_below_normal" : "roc_area_above_normal"]);
}

function chips(s: State) {
  const r = s.result!;
  const k = r.skill;
  const q = r.request;
  const box = el("div", "chips");
  const chip = (label: string, value: string) => {
    const c = el("span", "chip");
    if (label) c.append(label + " ");
    c.append(el("b", undefined, value));
    box.append(c);
  };
  chip("RPSS", num(k.rpss));
  chip("ROC below", roc(k, "bn"));
  chip("ROC above", roc(k, "an"));
  chip("Skill", r.highlights?.skill_verdict ?? "–");
  chip("", `${q.hindcast[0]}–${q.hindcast[1]} · leave-${q.cv_window}-out CV`);
  return box;
}

function highlightsBlock(s: State) {
  const h = s.result!.highlights;
  const box = el("div", "hl");
  box.append(el("div", "label", "Highlights"));
  const ul = el("ul");
  if (!h) { ul.append(el("li", undefined, "No highlights available.")); box.append(ul); return box; }
  const rs = h.region_shares;
  ul.append(el("li", undefined, `Above-normal dominant over ${pct(rs.above)} of the region, near-normal over ${pct(rs.normal)}, below-normal over ${pct(rs.below)}, no clear signal over ${pct(rs.none)}.`));
  if (h.strongest.above.length) ul.append(el("li", undefined, `Strongest wet signal: ${h.strongest.above.map((t) => `${t.name} (up to ${pct(t.max_prob)})`).join(", ")}.`));
  if (h.strongest.below.length) ul.append(el("li", undefined, `Strongest dry signal: ${h.strongest.below.map((t) => `${t.name} (up to ${pct(t.max_prob)})`).join(", ")}.`));
  box.append(ul);
  return box;
}

function methodSeg(s: State, a: Actions) {
  const seg = el("div", "seg");
  seg.setAttribute("role", "group");
  seg.setAttribute("aria-label", "Downscaling method");
  for (const [key, label] of METHODS) {
    const b = el("button", undefined, label);
    b.type = "button";
    b.setAttribute("aria-pressed", String((s.pending?.request.method ?? s.result?.request.method) === key));
    b.disabled = s.busy;
    b.onclick = () => a.setMethod(key);
    seg.append(b);
  }
  return seg;
}

function layerSeg(s: State, a: Actions) {
  const seg = el("div", "seg");
  seg.setAttribute("role", "group");
  seg.setAttribute("aria-label", "Layer");
  for (const [key, label] of LAYERS) {
    const b = el("button", undefined, label);
    b.type = "button";
    b.setAttribute("aria-pressed", String(s.layer === key));
    b.onclick = () => a.setLayer(key);
    seg.append(b);
  }
  return seg;
}

function button(label: string, onClick: () => void, disabled = false, cls = "btn") {
  const b = el("button", cls, label);
  b.type = "button";
  b.onclick = onClick;
  b.disabled = disabled;
  return b;
}

function errorBox(s: State, a: Actions) {
  const box = el("div", "err");
  box.append(el("div", "label", "The server reported an error"));
  box.append(el("code", undefined, s.error ?? ""));
  box.append(button("Retry", a.retry));
  return box;
}

export function renderReadout(s: State): HTMLElement {
  const box = el("div", "readout");
  const c = s.cursor;
  const st = s.grid?.style;
  box.append(el("div", "label", c ? `At cursor · ${c.lat.toFixed(1)}°, ${c.lon.toFixed(1)}°${c.country ? ` · ${c.country}` : ""}` : "Hover the map"));
  const bars = el("div", "bars");
  const rows: [string, number | null | undefined, string | undefined][] = [
    ["Below", c?.b, st?.below_colors[3]], ["Normal", c?.n, st?.normal_colors[3]], ["Above", c?.a, st?.above_colors[3]],
  ];
  for (const [name, v, col] of rows) {
    const row = el("div", "bar3");
    const i = el("i");
    i.style.width = `${Math.round(100 * (v ?? 0))}%`;
    i.style.background = col ?? "#999";
    row.append(el("span", undefined, name), i, el("span", undefined, pct(v)));
    bars.append(row);
  }
  box.append(bars);
  return box;
}

function skillTable(s: State) {
  const r = s.result!;
  const k = r.skill;
  const q = r.request;
  const t = el("table", "tbl");
  const rows: [string, string][] = [
    ["RPSS", num(k.rpss)],
    ["ROC below / above", `${roc(k, "bn")} / ${roc(k, "an")}`],
    ["Skill", r.highlights?.skill_verdict ?? "–"],
    ["Training", `${q.hindcast[0]}–${q.hindcast[1]}, leave-${q.cv_window}-out CV`],
  ];
  for (const [a, b] of rows) {
    const tr = el("tr");
    tr.append(el("td", undefined, a), el("td", undefined, b));
    t.append(tr);
  }
  return t;
}

/** Rebuild the DOM for the current state. The map is handed back through `holder`. */
export function render(root: HTMLElement, s: State, a: Actions, holder: MapHolder): void {
  root.replaceChildren();
  const existing = holder.map;     // reused so zoom/pan survive layer switches and panel toggles
  holder.map = null;
  if (s.error && !s.result) { root.append(errorBox(s, a)); return; }
  if (!s.result && !s.pending) {
    const card = el("div", "card");
    card.append(el("p", "sub", "Waiting for the outlook…"));
    root.append(card);
    return;
  }
  const { title, sub } = titleOf(s);
  const tip = el("div", "tip");
  tip.textContent = cursorText(s.cursor);
  tip.style.display = s.cursor ? "block" : "none";

  const buildMapArea = (cls: string) => {
    const area = el("div", `maparea ${cls}`);
    const map = existing ?? new OutlookMap(area);
    if (existing) {
      map.el.querySelectorAll(".skeleton, .tip, .err, .pendingnote").forEach((n) => n.remove());
      area.append(map.el);
    }
    holder.map = map;
    map.setZoomable(s.mode === "fullscreen");
    if (s.busy) map.el.append(el("div", "skeleton"));
    if (s.pending) map.el.append(el("div", "pendingnote", pendingMessage(s.pending.elapsed, methodLabel(s.pending.request.method))));
    map.el.append(tip);
    if (s.error) {
      const e = errorBox(s, a);
      e.style.position = "absolute";
      e.style.inset = "12px";
      map.el.append(e);
    }
    const bounds = s.result ? s.result.bounds : Array.isArray(s.pending!.request.region) ? (s.pending!.request.region as Bounds) : null;
    if (s.grid) map.setData(s.grid, s.layer); else if (bounds) map.setBounds(bounds);
    map.onHover = (c) => a.hover(c);
    return { area, map };
  };

  if (s.mode === "fullscreen") {
    const fs = el("div", "fs");
    const bar = el("div", "bar");
    const left = el("span");
    left.append(el("b", undefined, title), ` · ${sub}`);
    bar.append(left, el("span", undefined, "Fullscreen · Esc to return"));
    fs.append(bar);
    const body = el("div", `body${s.panelHidden ? " collapsed" : ""}`);
    const { area, map } = buildMapArea("fs-area");
    const zoom = el("div", "zoom");
    zoom.append(button("+", () => map.zoomBy(1.3)), button("−", () => map.zoomBy(1 / 1.3)), button("Fit", () => map.reset(), false, "btn fit"));
    area.append(zoom);
    if (s.panelHidden) area.append(button("Show panel ›", () => a.collapsePanel(false), false, "btn showpanel"));
    const divider = el("div", "divider");
    divider.title = "Drag to resize";
    const panel = el("div", "panel");
    const head = el("div", "head");
    head.append(el("span", "label", "Layer"), button("›", () => a.collapsePanel(true), false, "btn"));
    panel.append(head, layerSeg(s, a), renderReadout(s), skillTable(s), highlightsBlock(s));
    const foot = el("div", "ctrls");
    foot.append(methodSeg(s, a), button("Explain", a.explain, s.busy));
    panel.append(foot);
    body.append(area, divider, panel);
    fs.append(body);
    root.append(fs);
    const refit = () => map.fit(area.clientWidth - 24, area.clientHeight - 24);
    // divider drag resizes the panel between 200 and 520px
    let dragging = false;
    divider.onpointerdown = (e) => { dragging = true; divider.classList.add("active"); divider.setPointerCapture(e.pointerId); };
    const stop = () => { dragging = false; divider.classList.remove("active"); };
    divider.onpointerup = stop;
    divider.onpointercancel = stop;
    divider.onpointermove = (e) => {
      if (!dragging) return;
      const right = body.getBoundingClientRect().right;
      const w = Math.min(520, Math.max(200, right - e.clientX));
      body.style.gridTemplateColumns = `1fr 6px ${w}px`;
      refit();
    };
    new ResizeObserver(refit).observe(area);
    refit();
    return;
  }

  const card = el("div", "card");
  card.append(el("p", "title", title), el("p", "sub", sub));
  const { area, map } = buildMapArea("inline-area");
  if (!s.result) {                     // still computing: the card fills in when the server is done
    card.append(area);
    root.append(card);
    const fitPending = () => { const { w, h } = inlineMapArea(card.clientWidth); map.fit(w, h); };
    new ResizeObserver(fitPending).observe(card);
    fitPending();
    return;
  }
  card.append(area, legend(s), chips(s), highlightsBlock(s));
  const ctrls = el("div", "ctrls");
  const actions = el("div", "actions");
  actions.append(button("Explain", a.explain, s.busy));
  if (s.fullscreenAvailable) actions.append(button("Expand", a.expand));
  ctrls.append(methodSeg(s, a), actions);
  card.append(ctrls);
  root.append(card);
  const fitInline = () => {
    const { w, h } = inlineMapArea(card.clientWidth);
    map.fit(w, h);
  };
  new ResizeObserver(fitInline).observe(card);
  fitInline();
}
