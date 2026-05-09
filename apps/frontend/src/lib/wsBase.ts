/**
 * Resolve the WebSocket base URL — Phase 2 PR #9 task 2.10.
 *
 * Mirrors `lib/apiBase.ts` but converts the http(s) scheme to its websocket
 * counterpart so the rest of the app can build paths without re-deriving the
 * scheme. We never cache this at module scope — `import.meta.env` is read at
 * call time (CLAUDE.md 핵심 규칙 §11).
 */

/**
 * Convert an HTTP/HTTPS URL into its WebSocket equivalent.
 *
 * Inputs that already use ws/wss are passed through unchanged so an
 * operator override (e.g. `VITE_WS_BASE_URL`) is a future-proofed escape
 * hatch.
 */
// nosemgrep: javascript.lang.security.detect-insecure-websocket.detect-insecure-websocket
// — `ws://` IS the unencrypted scheme, but it is reachable only when the
// caller passes an `http://` base (i.e. local dev against the Vite proxy
// or the bundled docker-compose stack). Production deployments always
// provide an `https://` VITE_API_BASE_URL, which falls through the first
// branch and yields `wss://`. Stripping ws:// here would break the dev
// experience without improving real-world security.
export function httpToWs(httpUrl: string): string {
  if (httpUrl.startsWith("https://")) {
    return "wss://" + httpUrl.slice("https://".length);
  }
  if (httpUrl.startsWith("http://")) {
    return "ws://" + httpUrl.slice("http://".length);
  }
  // Already wss:// / ws:// — caller handed us a ready-made base.
  if (httpUrl.startsWith("wss://") || httpUrl.startsWith("ws://")) {
    return httpUrl;
  }
  // Unknown shape — return as-is so a misconfiguration surfaces loudly.
  return httpUrl;
}

/**
 * Resolve the WebSocket base URL by reading the same environment variable
 * the axios instance reads, then translating http→ws / https→wss. Trailing
 * slashes are stripped so concatenated paths never produce `//`.
 */
export function resolveWebSocketBaseUrl(): string {
  const raw =
    (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
    "http://localhost:8000";
  const trimmed = raw.replace(/\/+$/, "");
  return httpToWs(trimmed);
}

/**
 * Build the per-scan WebSocket URL — `/ws/scans/{scan_id}` per the contract
 * pinned in apps/backend/api/v1/ws.py.
 */
export function buildScanProgressUrl(scanId: string): string {
  return `${resolveWebSocketBaseUrl()}/ws/scans/${encodeURIComponent(scanId)}`;
}
