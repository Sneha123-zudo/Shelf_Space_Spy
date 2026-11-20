import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Where /api is proxied in dev and preview. Override to point the UI at another API (e.g. a throwaway copy).
const apiTarget = process.env.VITE_API_PROXY ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": apiTarget,
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
