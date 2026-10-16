// Generic map/image card: shows a PNG output handle (from africas2s.plot_field,
// plot_terciles, or any other plot step) as an MCP App. The model receives only
// the handle, caption and sizes via view_output; the bytes come to the widget
// through the app-only output_image tool, so they never enter the model's context.
import { App, applyDocumentTheme, applyHostStyleVariables, type McpUiHostContext } from "@modelcontextprotocol/ext-apps";

type ViewResult = { handle: string; title?: string | null; label?: string | null; media_type: string;
  size_bytes?: number | null; download_url?: string | null };
type ImagePayload = { handle: string; media_type: string; data_base64: string };

const root = document.getElementById("root")!;
const app = new App({ name: "s2s-image", version: "0.1.0" }, {}, { autoResize: true });
let result: ViewResult | null = null;
let src: string | null = null;
let error: string | null = null;
let fullscreen = false;
let canFullscreen = false;

function applyContext(ctx: McpUiHostContext | undefined) {
  if (!ctx) return;
  if (ctx.theme) applyDocumentTheme(ctx.theme);
  if (ctx.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
  canFullscreen = (ctx.availableDisplayModes ?? []).includes("fullscreen");
  fullscreen = ctx.displayMode === "fullscreen";
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string, text?: string) {
  const e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e;
}

function render() {
  root.innerHTML = "";
  root.classList.toggle("full", fullscreen);
  if (error) { root.append(el("div", "error", error)); return; }
  if (!result) { root.append(el("div", "note", "Loading map…")); return; }
  const bar = el("div", "bar");
  bar.append(el("div", "title", result.title || result.label || result.handle));
  const actions = el("div", "actions");
  if (canFullscreen) {
    const b = el("button", undefined, fullscreen ? "Close" : "Expand");
    b.onclick = async () => {
      try { const r = await app.requestDisplayMode({ mode: fullscreen ? "inline" : "fullscreen" });
        fullscreen = r.mode === "fullscreen"; render(); } catch { /* host refused */ }
    };
    actions.append(b);
  }
  if (result.download_url) {
    const a = el("a", undefined, "Open PNG"); a.href = result.download_url; a.target = "_blank"; a.rel = "noopener";
    actions.append(a);
  }
  bar.append(actions);
  root.append(bar);
  if (src) { const img = el("img"); img.src = src; img.alt = result.title || result.label || "map"; root.append(img); }
  else root.append(el("div", "note", "Fetching image…"));
}

async function load(r: ViewResult) {
  result = r; error = null; src = null; render();
  try {
    const got = await app.callServerTool({ name: "output_image", arguments: { handle: r.handle } });
    if (got.isError) throw new Error(got.content.map((c) => (c.type === "text" ? c.text : "")).join("\n") || "output_image failed");
    const p = got.structuredContent as ImagePayload;
    src = `data:${p.media_type};base64,${p.data_base64}`;
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }
  render();
}

app.ontoolresult = (r) => {
  if (r.isError) { error = r.content.map((c) => (c.type === "text" ? c.text : "")).join("\n") || "Tool call failed."; render(); return; }
  void load(r.structuredContent as ViewResult);
};
app.onhostcontextchanged = (ctx) => { applyContext({ ...(app.getHostContext() ?? {}), ...ctx }); render(); };

(async () => {
  render();
  try { await app.connect(); applyContext(app.getHostContext()); render(); }
  catch (e) { error = e instanceof Error ? e.message : String(e); render(); }
})();
