/**
 * Security headers — clickjacking defense (BUG-009).
 *
 * Guards that the two anti-framing headers are present in BOTH delivery paths:
 *   - production nginx (`nginx/default.conf`)
 *   - the Vite dev server (`vite.config.ts` server.headers)
 *
 * nginx does NOT inherit `add_header` into a `location` that declares its own
 * `add_header`, so the prod check additionally verifies the headers appear in
 * every header-bearing location (assets, locales, healthz, SPA fallback).
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const ROOT = path.resolve(__dirname, "../..");

function read(rel: string): string {
  return readFileSync(path.join(ROOT, rel), "utf8");
}

describe("clickjacking headers (BUG-009)", () => {
  it("nginx default.conf sets both headers in the SPA fallback location", () => {
    const conf = read("nginx/default.conf");
    // The `/` location is the framed SPA surface.
    const spa = conf.slice(conf.indexOf("location / {"));
    expect(spa).toContain('add_header X-Frame-Options "SAMEORIGIN" always;');
    expect(spa).toContain(
      `add_header Content-Security-Policy "frame-ancestors 'self'" always;`,
    );
  });

  it("nginx repeats the headers in every header-bearing location (no inheritance gap)", () => {
    const conf = read("nginx/default.conf");
    const xfoCount = conf.split('add_header X-Frame-Options "SAMEORIGIN" always;')
      .length - 1;
    const cspCount =
      conf.split(
        `add_header Content-Security-Policy "frame-ancestors 'self'" always;`,
      ).length - 1;
    // healthz + assets + locales + SPA fallback = 4 locations declare headers.
    expect(xfoCount).toBeGreaterThanOrEqual(4);
    expect(cspCount).toBeGreaterThanOrEqual(4);
    expect(xfoCount).toBe(cspCount);
  });

  it("vite dev server sets the same two headers for dev/prod parity", () => {
    const cfg = read("vite.config.ts");
    expect(cfg).toContain('"X-Frame-Options": "SAMEORIGIN"');
    expect(cfg).toContain(
      `"Content-Security-Policy": "frame-ancestors 'self'"`,
    );
  });
});
