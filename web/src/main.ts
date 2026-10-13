// Host wiring: receives seasonal_outlook's result from the host, fetches the
// grid through the app-only outlook_grid tool, re-runs the recipe on method
// switches, and keeps the model's context in step with what is on screen.
import { withTimeout } from "./async";
import { App, applyDocumentTheme, applyHostStyleVariables, type McpUiHostContext } from "@modelcontextprotocol/ext-apps";
import { fullscreenAvailable } from "./geometry";
import type { Layer } from "./palette";
import { cursorText, methodLabel, render, renderReadout, titleOf, type Actions, type MapHolder } from "./ui";
import { pendingMessage } from "./pending";
import type { GridPayload, OutlookRequest, OutlookResult, RunningResult, State } from "./types";

type ToolResult = Awaited<ReturnType<App["callServerTool"]>>;

const root = document.getElementById("root")!;
const state: State = {
  result: null, grid: null, layer: "dominant", mode: "inline", fullscreenAvailable: false,
  busy: false, pending: null, error: null, cursor: null, panelHidden: false,
};
const holder: MapHolder = { map: null };
const app = new App({ name: "s2s-outlook", version: "0.1.0" }, {}, { autoResize: true });

function applyContext(ctx: McpUiHostContext | undefined) {
  if (!ctx) return;
  if (ctx.theme) applyDocumentTheme(ctx.theme);
  if (ctx.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
  state.fullscreenAvailable = fullscreenAvailable(ctx.availableDisplayModes);
  state.mode = ctx.displayMode === "fullscreen" ? "fullscreen" : "inline";
}

function errorText(r: ToolResult): string | null {
  if (!r.isError) return null;
  const text = r.content.map((c) => (c.type === "text" ? c.text : "")).filter(Boolean).join("\n");
  return text || "Tool call failed.";
}

/** Longer than a cached answer ever needs, shorter than a user will wait for a frozen card. */
const METHOD_SWITCH_TIMEOUT_MS = 75_000;
let retryMethod: string | null = null;   // the method a failed switch was trying to reach
let lastRequest: OutlookRequest | null = null;

// ---- polling while the server is still computing (status "running") ----
const POLL_WAIT_SECONDS = 10;            // server-side wait per poll: well inside any host's call budget
const POLL_GIVE_UP_MS = 60 * 60_000;      // a cold region can spend 20+ min fetching observations
let pollTimer: ReturnType<typeof setTimeout> | null = null;
let ticker: ReturnType<typeof setInterval> | null = null;
let pollGeneration = 0;

function stopPolling() {
  pollGeneration++;
  if (pollTimer) clearTimeout(pollTimer);
  if (ticker) clearInterval(ticker);
  pollTimer = ticker = null;
}

function startPolling(run: RunningResult) {
  stopPolling();
  const gen = pollGeneration;
  const startedAt = Date.now() - run.elapsed_seconds * 1000;
  lastRequest = run.request;
  state.pending = { request: run.request, elapsed: run.elapsed_seconds };
  state.busy = true;
  state.error = null;
  draw();
  ticker = setInterval(() => {               // keep the elapsed time honest between polls, in place
    const note = root.querySelector<HTMLElement>(".pendingnote");
    if (note && state.pending) note.textContent = pendingMessage((Date.now() - startedAt) / 1000, methodLabel(state.pending.request.method));
  }, 1000);
  const poll = async () => {
    if (gen !== pollGeneration) return;
    if (Date.now() - startedAt > POLL_GIVE_UP_MS) {
      stopPolling();
      state.pending = null;
      fail(new Error("This is taking longer than expected. Ask again in a few minutes; the server keeps the result once it finishes."));
      return;
    }
    try {
      const r = await withTimeout(
        app.callServerTool({ name: "seasonal_outlook", arguments: { ...run.request, wait_seconds: POLL_WAIT_SECONDS } }),
        METHOD_SWITCH_TIMEOUT_MS, "The host stopped answering while the outlook was computing. Try again shortly.");
      if (gen !== pollGeneration) return;
      const err = errorText(r);
      if (err) throw new Error(err);
      const sc = r.structuredContent as OutlookResult | RunningResult;
      if (sc.status === "running") {
        state.pending = { request: sc.request, elapsed: sc.elapsed_seconds };
        pollTimer = setTimeout(poll, Math.max(1, sc.poll_after_seconds) * 1000);
        return;
      }
      stopPolling();
      await acceptResult(sc as OutlookResult);
    } catch (e) {
      stopPolling();
      state.pending = null;
      retryMethod = run.request.method;
      fail(e);
    }
  };
  pollTimer = setTimeout(poll, Math.max(1, run.poll_after_seconds) * 1000);
}

/** A tool result from the host or from our own call: either the outlook, or "still running". */
function handleResult(sc: unknown): Promise<void> {
  const r = sc as OutlookResult | RunningResult;
  if (r && r.status === "running") { startPolling(r); return Promise.resolve(); }
  stopPolling();
  return acceptResult(r as OutlookResult);
}

function fail(e: unknown) {
  state.error = e instanceof Error ? e.message : String(e);
  state.busy = false;
  draw();
}

function draw() { render(root, state, actions, holder); }

/** Cheap hover path: update the tip and the fullscreen readout without rebuilding the card. */
function updateCursorOnly() {
  const tip = root.querySelector<HTMLElement>(".map .tip");
  if (tip) {
    tip.textContent = cursorText(state.cursor);
    tip.style.display = state.cursor ? "block" : "none";
  }
  const ro = root.querySelector<HTMLElement>(".readout");
  if (ro) ro.replaceWith(renderReadout(state));
}

async function acceptResult(result: OutlookResult) {
  state.result = result;
  state.pending = null;
  state.grid = null;
  state.busy = true;
  state.error = null;
  state.cursor = null;
  draw();
  const r = await app.callServerTool({ name: "outlook_grid", arguments: { handle: result.tercile_forecast } });
  const err = errorText(r);
  if (err) throw new Error(err);
  state.grid = r.structuredContent as GridPayload;
  state.busy = false;
  draw();
  void pushContext();
}

async function pushContext() {
  if (!state.result) return;
  const q = state.result.request;
  const rpss = state.result.skill.rpss;
  try {
    await app.updateModelContext({ content: [{ type: "text", text:
      `S2S outlook widget now shows: ${titleOf(state).title}; method ${q.method}; layer ${state.layer}; ` +
      `RPSS ${typeof rpss === "number" ? rpss.toFixed(2) : "n/a"}. ${state.result.summary}` +
      (state.result.palette ? ` ${state.result.palette}` : "") }] });
  } catch { /* hosts without model-context support: nothing to do */ }
}

const actions: Actions = {
  async setMethod(method: string) {
    if (!state.result || state.busy) return;
    if (state.result.request.method === method && !state.error) return;
    state.busy = true;
    state.error = null;
    draw();
    try {
      const r = await withTimeout(
        app.callServerTool({ name: "seasonal_outlook", arguments: { ...state.result.request, method } }),
        METHOD_SWITCH_TIMEOUT_MS,
        "The server is still computing this method; the first run for a region can take a minute or two. " +
        "Finished results are cached, so try again shortly.");
      const err = errorText(r);
      if (err) throw new Error(err);
      retryMethod = null;
      lastRequest = { ...state.result.request, method };
      await handleResult(r.structuredContent);
    } catch (e) { retryMethod = method; fail(e); }
  },
  setLayer(layer: Layer) {
    state.layer = layer;
    holder.map?.setLayer(layer);
    draw();
    void pushContext();
  },
  async explain() {
    if (!state.result) return;
    const label = methodLabel(state.result.request.method);
    try {
      await app.sendMessage({ role: "user", content: [{ type: "text", text:
        `Explain the outlook currently shown in the S2S widget (${titleOf(state).title}; method ${label}; layer ${state.layer}) ` +
        `in two or three sentences for a non-specialist, using its highlights: ${state.result.summary} Do not repeat the card's numbers.` }] });
    } catch (e) { fail(e); }
  },
  async expand() {
    try {
      const r = await app.requestDisplayMode({ mode: "fullscreen" });
      state.mode = r.mode === "fullscreen" ? "fullscreen" : "inline";
      draw();
    } catch { /* host refused; stay inline */ }
  },
  collapsePanel(hidden: boolean) { state.panelHidden = hidden; draw(); },
  retry() {
    if (state.result) { void actions.setMethod(retryMethod ?? state.result.request.method); return; }
    if (lastRequest) startPolling({ status: "running", request: lastRequest, elapsed_seconds: 0, poll_after_seconds: 1, message: "" });
  },
  hover(cell) {
    if (!cell || !state.grid) {
      state.cursor = null;
    } else {
      const k = cell.i * state.grid.lon.length + cell.j;
      state.cursor = {
        lat: cell.lat, lon: cell.lon, country: holder.map?.countryAt(cell.lon, cell.lat) ?? null,
        b: state.grid.below[k], n: state.grid.normal[k], a: state.grid.above[k],
      };
    }
    updateCursorOnly();
  },
};

app.ontoolresult = (r) => {
  const err = errorText(r);
  if (err) { stopPolling(); state.error = err; state.busy = false; draw(); return; }
  handleResult(r.structuredContent).catch(fail);
};
app.onhostcontextchanged = (ctx) => {
  applyContext({ ...(app.getHostContext() ?? {}), ...ctx });
  draw();
};
app.ontoolcancelled = () => { stopPolling(); state.pending = null; state.busy = false; draw(); };

(async () => {
  draw();
  try {
    await app.connect();
    applyContext(app.getHostContext());
  } catch (e) {
    // Outside an MCP host (opened as a plain file) there is nothing to connect to; keep the empty card.
    console.warn("s2s-outlook: not connected to an MCP host", e);
  }
  draw();
})();
