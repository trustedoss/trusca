/**
 * wsBase — unit tests (PR #9 task 2.10).
 *
 * `resolveWebSocketBaseUrl` reads `import.meta.env.VITE_API_BASE_URL` at
 * call time; we stub it via `vi.stubEnv` to drive every code path.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildScanProgressUrl,
  httpToWs,
  resolveWebSocketBaseUrl,
} from "@/lib/wsBase";

describe("wsBase.httpToWs", () => {
  it("converts http to ws", () => {
    expect(httpToWs("http://localhost:8000")).toBe("ws://localhost:8000");
  });
  it("converts https to wss", () => {
    expect(httpToWs("https://api.example.com")).toBe("wss://api.example.com");
  });
  it("passes through ws:// untouched", () => {
    expect(httpToWs("ws://x:8000")).toBe("ws://x:8000");
  });
  it("passes through wss:// untouched", () => {
    expect(httpToWs("wss://x:8000")).toBe("wss://x:8000");
  });
  it("returns unknown shapes as-is so misconfig surfaces", () => {
    expect(httpToWs("ftp://x")).toBe("ftp://x");
  });
});

describe("wsBase.resolveWebSocketBaseUrl", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("returns the configured env value translated to ws", () => {
    vi.stubEnv("VITE_API_BASE_URL", "http://localhost:9999");
    expect(resolveWebSocketBaseUrl()).toBe("ws://localhost:9999");
  });

  it("strips trailing slashes before scheme conversion", () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com//");
    expect(resolveWebSocketBaseUrl()).toBe("wss://api.example.com");
  });
});

describe("wsBase.buildScanProgressUrl", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("builds /ws/scans/<scan_id> off the resolved base", () => {
    vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8000");
    expect(buildScanProgressUrl("abc123")).toBe(
      "ws://localhost:8000/ws/scans/abc123",
    );
  });

  it("encodes the scan id", () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com");
    expect(buildScanProgressUrl("a/b c")).toBe(
      "wss://api.example.com/ws/scans/a%2Fb%20c",
    );
  });
});
