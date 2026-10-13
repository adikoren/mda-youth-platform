import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Same-origin in development: the browser talks to Vite, Vite proxies /api to FastAPI.
// This keeps cookies first-party (HttpOnly session + CSRF double-submit) without CORS.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.MDA_API_URL ?? "http://127.0.0.1:8000", changeOrigin: false } },
  },
  // `npm run preview` serves the production build (with the service worker) on the same origin.
  preview: {
    port: 4173,
    proxy: { "/api": { target: process.env.MDA_API_URL ?? "http://127.0.0.1:8000", changeOrigin: false } },
  },
});
