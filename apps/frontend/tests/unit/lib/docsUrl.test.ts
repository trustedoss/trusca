/**
 * The documentation link the app header now offers (C1).
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { docsUrl } from "@/lib/docsUrl";

describe("docsUrl", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("falls back to the public documentation site", () => {
    expect(docsUrl()).toBe("https://trustedoss.github.io/trusca/");
  });

  it("honours a self-hosted mirror", () => {
    vi.stubEnv("VITE_DOCS_URL", "https://docs.internal/trusca/");
    expect(docsUrl()).toBe("https://docs.internal/trusca/");
  });

  it("treats a blank override as unset", () => {
    // An empty value in an .env file is how an operator most often ends up
    // here, and an empty href would silently reload the current page.
    vi.stubEnv("VITE_DOCS_URL", "   ");
    expect(docsUrl()).toBe("https://trustedoss.github.io/trusca/");
  });
});
