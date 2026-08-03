/**
 * demoSandbox helpers — feat/demo-sandbox-scan.
 *
 * The name matcher gates which project re-opens its write affordances in the
 * sandbox demo, and the URL resolver honors an optional deployer override, so
 * both are unit-guarded here.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  bomLensUrl,
  DEMO_SANDBOX_PROJECT_NAME,
  DEMO_SANDBOX_SCAN_MAX_MB,
  isDemoSandboxProjectName,
} from "@/lib/demoSandbox";

describe("isDemoSandboxProjectName", () => {
  it("matches the exact seeded name (trimming surrounding whitespace)", () => {
    expect(isDemoSandboxProjectName(DEMO_SANDBOX_PROJECT_NAME)).toBe(true);
    expect(isDemoSandboxProjectName("  Demo Sandbox  ")).toBe(true);
  });

  it("does not match a different name, wrong case, null, or undefined", () => {
    expect(isDemoSandboxProjectName("demo sandbox")).toBe(false);
    expect(isDemoSandboxProjectName("Demo Sandbox 2")).toBe(false);
    expect(isDemoSandboxProjectName("kwg-directory")).toBe(false);
    expect(isDemoSandboxProjectName(null)).toBe(false);
    expect(isDemoSandboxProjectName(undefined)).toBe(false);
  });

  it("exposes a positive live-scan size cap for the copy", () => {
    expect(DEMO_SANDBOX_SCAN_MAX_MB).toBeGreaterThan(0);
  });
});

describe("bomLensUrl", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("falls back to the public default when the env override is unset", () => {
    expect(bomLensUrl()).toMatch(/^https?:\/\//);
  });

  it("honors a VITE_BOMLENS_URL override (internal mirror)", () => {
    vi.stubEnv("VITE_BOMLENS_URL", "https://mirror.internal/bomlens");
    expect(bomLensUrl()).toBe("https://mirror.internal/bomlens");
  });

  it("ignores a blank override", () => {
    vi.stubEnv("VITE_BOMLENS_URL", "   ");
    expect(bomLensUrl()).toMatch(/^https?:\/\//);
  });
});
