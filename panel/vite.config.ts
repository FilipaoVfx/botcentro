import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// En desarrollo, /v1 se reenvía al BFF de Python (uvicorn en :8710).
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/v1": "http://127.0.0.1:8710", "/health": "http://127.0.0.1:8710" } },
  build: { sourcemap: false, assetsInlineLimit: 0 },
});
