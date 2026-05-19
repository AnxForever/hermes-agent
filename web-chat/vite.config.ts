import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

// In dev the Hermes data-plane API server runs on :8642 (aiohttp). The
// dashboard server (:9119) is the *admin* side and not what this app
// talks to. /v1/* and /api/v1/* both terminate at the gateway.
const GATEWAY = process.env.HERMES_GATEWAY_URL ?? "http://127.0.0.1:8642";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      // Resolve the shared design-tokens package without requiring an
      // npm workspace install. Works both in monorepo (workspace symlink
      // is identical) and in environments that build without `npm install`
      // at the repo root.
      "@hermes/design-tokens/tokens.css": path.resolve(
        __dirname,
        "../packages/design-tokens/src/tokens.css",
      ),
      "@hermes/design-tokens": path.resolve(
        __dirname,
        "../packages/design-tokens/src",
      ),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/v1": {
        target: GATEWAY,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
