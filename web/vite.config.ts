import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// Builds the widget into one self-contained HTML file inside the Python
// package so the server (and the wheel) ship it without Node at runtime.
export default defineConfig({
  plugins: [viteSingleFile()],
  build: {
    outDir: "../src/s2s_mcp/web",
    emptyOutDir: false,
    rollupOptions: { input: "outlook.html" },
    target: "es2022",
    cssCodeSplit: false,
    assetsInlineLimit: 100_000_000,
  },
});
