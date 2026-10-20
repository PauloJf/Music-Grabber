import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, Vite serves the SPA on :5173 and proxies the API to a running Music Grabber on
// :8099. In production the API process serves frontend/dist itself, so this is dev-only.
const API = "http://127.0.0.1:8099";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: { "/api": { target: API, changeOrigin: true }, "/healthz": { target: API, changeOrigin: true } },
  },
});
