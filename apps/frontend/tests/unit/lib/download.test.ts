/**
 * download helpers — unit tests (G2 frontend).
 *
 * Covers the shared browser-download primitives consumed by the NOTICE / SBOM
 * / vulnerability-PDF download surfaces.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  parseContentDispositionFilename,
  safeFilenameToken,
  triggerBlobDownload,
} from "@/lib/download";

describe("safeFilenameToken", () => {
  it("collapses unsafe characters to hyphens", () => {
    expect(safeFilenameToken("My Project!")).toBe("My-Project");
  });

  it("preserves dots, underscores, and hyphens", () => {
    expect(safeFilenameToken("a.b_c-d")).toBe("a.b_c-d");
  });

  it("trims leading/trailing hyphens", () => {
    expect(safeFilenameToken("  spaced  ")).toBe("spaced");
  });

  it("falls back to 'project' for empty/unsafe-only input", () => {
    expect(safeFilenameToken("   ")).toBe("project");
    expect(safeFilenameToken("@@@")).toBe("project");
  });
});

describe("parseContentDispositionFilename", () => {
  it("extracts a quoted filename", () => {
    expect(
      parseContentDispositionFilename('attachment; filename="report.pdf"'),
    ).toBe("report.pdf");
  });

  it("extracts a bare filename", () => {
    expect(
      parseContentDispositionFilename("attachment; filename=report.pdf"),
    ).toBe("report.pdf");
  });

  it("returns null for empty / missing headers", () => {
    expect(parseContentDispositionFilename(null)).toBeNull();
    expect(parseContentDispositionFilename(undefined)).toBeNull();
    expect(parseContentDispositionFilename("attachment")).toBeNull();
  });
});

describe("triggerBlobDownload", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("creates an object URL and clicks a transient anchor", () => {
    vi.useFakeTimers();
    // jsdom does not implement the URL object-URL APIs — install stubs.
    const createUrl = vi.fn().mockReturnValue("blob:fake");
    const revokeUrl = vi.fn();
    vi.stubGlobal("URL", {
      createObjectURL: createUrl,
      revokeObjectURL: revokeUrl,
    });
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});

    triggerBlobDownload(
      new Blob(["%PDF"], { type: "application/pdf" }),
      "report.pdf",
    );

    expect(createUrl).toHaveBeenCalledTimes(1);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    // Anchor is removed from the DOM after the click.
    expect(document.querySelector('a[download="report.pdf"]')).toBeNull();

    // Revocation is deferred ~1s.
    vi.advanceTimersByTime(1_000);
    expect(revokeUrl).toHaveBeenCalledWith("blob:fake");
  });
});
