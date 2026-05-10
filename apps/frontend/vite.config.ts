/// <reference types="vitest/config" />
import path from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    watch: {
      // Polling is required when source is bind-mounted from a host with a
      // different filesystem (macOS/Windows → Linux container).
      usePolling: true,
      interval: 500,
    },
    // Backend proxy: SPA + Playwright harness both fetch /v1/*, /auth/*,
    // and /ws/* through the Vite origin so cross-origin cookie / CORS
    // overhead does not leak into dev. The proxy target defaults to the
    // docker-compose service name `backend`; on a host-only run override
    // with VITE_PROXY_BACKEND=http://localhost:8000.
    proxy: {
      "/v1": {
        target: process.env.VITE_PROXY_BACKEND ?? "http://backend:8000",
        changeOrigin: true,
      },
      "/auth": {
        target: process.env.VITE_PROXY_BACKEND ?? "http://backend:8000",
        changeOrigin: true,
      },
      "/ws": {
        // nosemgrep: javascript.lang.security.detect-insecure-websocket.detect-insecure-websocket
        // — `ws://` IS the unencrypted scheme, but it is the dev-stack default
        // (the Vite dev server is the only consumer of this config; production
        // builds the SPA into static assets and ships them through Traefik
        // with `wss://`). The same rationale and `nosemgrep` suppression
        // applies to `apps/frontend/src/lib/wsBase.ts` for the runtime path.
        target: process.env.VITE_PROXY_WS ?? "ws://backend:8000",
        ws: true,
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    css: true,
    include: ["tests/unit/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["tests/e2e/**", "node_modules/**", "dist/**"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html", "lcov"],
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/**/*.d.ts",
        "src/main.tsx",
        "src/vite-env.d.ts",
        "src/locales/**",
        // Pure type-only modules (no runtime exports). v8 coverage reports
        // them at 0% even though tsc strips them at build time.
        "src/types/**",
      ],
      // CLAUDE.md 품질·보안·운영 표준 §2: PR 머지 게이트는 신규/변경 코드
      // line coverage ≥ 80%. 부트스트랩 단계에서는 전역 라인 임계로 근사.
      thresholds: {
        lines: 80,
        functions: 80,
        statements: 80,
        branches: 70,
      },
    },
  },
});
