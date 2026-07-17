/**
 * SbomTab — unit tests (Step 4-A).
 *
 * Coverage targets:
 *   - All four download buttons render with stable testids.
 *   - Clicking a button calls `downloadSbom` with the matching format.
 *   - Successful download triggers `URL.createObjectURL` (the proxy for
 *     "the browser was handed a blob to save").
 *   - API error surfaces as an inline alert.
 *   - `last_scan_at` formatting / fallback message both render.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SbomTab } from "@/features/projects/components/SbomTab";
import { ProblemError } from "@/lib/problem";

vi.mock("@/lib/projectsApi", async () => {
  const actual = await vi.importActual<
    typeof import("@/lib/projectsApi")
  >("@/lib/projectsApi");
  return {
    ...actual,
    downloadSbom: vi.fn(),
  };
});

import { downloadSbom } from "@/lib/projectsApi";
const mockedDownloadSbom = vi.mocked(downloadSbom);

const FORMATS: Array<{ format: string; testid: string }> = [
  { format: "cyclonedx-json", testid: "sbom-download-cyclonedx-json" },
  { format: "cyclonedx-xml", testid: "sbom-download-cyclonedx-xml" },
  { format: "spdx-json", testid: "sbom-download-spdx-json" },
  { format: "spdx-tv", testid: "sbom-download-spdx-tv" },
];

describe("SbomTab", () => {
  let originalCreateObjectURL: unknown;
  let originalRevokeObjectURL: unknown;

  beforeEach(() => {
    mockedDownloadSbom.mockReset();
    originalCreateObjectURL = (URL as unknown as Record<string, unknown>)
      .createObjectURL;
    originalRevokeObjectURL = (URL as unknown as Record<string, unknown>)
      .revokeObjectURL;
    (URL as unknown as Record<string, unknown>).createObjectURL = vi
      .fn()
      .mockReturnValue("blob:fake-url");
    (URL as unknown as Record<string, unknown>).revokeObjectURL = vi.fn();
  });

  afterEach(() => {
    if (originalCreateObjectURL === undefined) {
      delete (URL as unknown as Record<string, unknown>).createObjectURL;
    } else {
      (URL as unknown as Record<string, unknown>).createObjectURL =
        originalCreateObjectURL;
    }
    if (originalRevokeObjectURL === undefined) {
      delete (URL as unknown as Record<string, unknown>).revokeObjectURL;
    } else {
      (URL as unknown as Record<string, unknown>).revokeObjectURL =
        originalRevokeObjectURL;
    }
  });

  it("renders the SBOM tab card and all four download buttons", () => {
    render(<SbomTab projectId="proj-1" />);
    expect(screen.getByTestId("sbom-tab")).toBeInTheDocument();
    for (const { testid } of FORMATS) {
      expect(screen.getByTestId(testid)).toBeInTheDocument();
    }
  });

  it("renders the no-scan-yet message when lastScanAt is null", () => {
    render(<SbomTab projectId="proj-1" lastScanAt={null} />);
    expect(screen.getByTestId("sbom-no-scan")).toBeInTheDocument();
    expect(screen.queryByTestId("sbom-last-scan")).not.toBeInTheDocument();
  });

  it("renders the last-scan timestamp when lastScanAt is provided", () => {
    render(
      <SbomTab projectId="proj-1" lastScanAt="2026-05-08T12:34:00Z" />,
    );
    expect(screen.getByTestId("sbom-last-scan")).toBeInTheDocument();
    expect(screen.queryByTestId("sbom-no-scan")).not.toBeInTheDocument();
  });

  it("labels 'Latest scan' with the (succeeded) timestamp it is given", () => {
    // FIX 3: ProjectDetailPage now passes `last_succeeded_scan_at` here. The
    // SbomTab renders the year of whatever timestamp it receives, so the label
    // reflects the scan the SBOM is actually exported from.
    render(<SbomTab projectId="proj-1" lastScanAt="2026-05-22T03:00:00Z" />);
    const label = screen.getByTestId("sbom-last-scan");
    expect(label.textContent).toContain("2026");
    expect(screen.queryByTestId("sbom-no-scan")).not.toBeInTheDocument();
  });

  it("invokes downloadSbom with the matching format for each button", async () => {
    mockedDownloadSbom.mockResolvedValue({
      blob: new Blob(["{}"], { type: "application/json" }),
      filename: "sbom-proj-1.cdx.json",
      format: "cyclonedx-json",
    });
    const user = userEvent.setup();
    render(<SbomTab projectId="proj-1" />);
    await user.click(screen.getByTestId("sbom-download-cyclonedx-json"));
    await waitFor(() => {
      expect(mockedDownloadSbom).toHaveBeenCalledWith(
        "proj-1",
        "cyclonedx-json",
        // feature #28: the SBOM tab threads the (possibly undefined) pinned scan
        // id through as the third arg so a historical snapshot exports its own SBOM.
        { scanId: undefined },
      );
    });
    // Blob was handed off to the browser via createObjectURL.
    expect(
      (URL as unknown as { createObjectURL: { mock: { calls: unknown[] } } })
        .createObjectURL.mock.calls.length,
    ).toBeGreaterThanOrEqual(1);
  });

  it("invokes downloadSbom with the spdx-tv format when the SPDX TV button is clicked", async () => {
    mockedDownloadSbom.mockResolvedValue({
      blob: new Blob(["x"], { type: "text/plain" }),
      filename: "sbom-proj-1.spdx",
      format: "spdx-tv",
    });
    const user = userEvent.setup();
    render(<SbomTab projectId="proj-1" />);
    await user.click(screen.getByTestId("sbom-download-spdx-tv"));
    await waitFor(() => {
      expect(mockedDownloadSbom).toHaveBeenCalledWith("proj-1", "spdx-tv", {
        scanId: undefined,
      });
    });
  });

  it("threads the pinned scan id into downloadSbom in historical mode (feature #28)", async () => {
    mockedDownloadSbom.mockResolvedValue({
      blob: new Blob(["{}"], { type: "application/json" }),
      filename: "sbom-proj-1.cdx.json",
      format: "cyclonedx-json",
    });
    const user = userEvent.setup();
    render(<SbomTab projectId="proj-1" scanId="scan-old" />);
    await user.click(screen.getByTestId("sbom-download-cyclonedx-json"));
    await waitFor(() => {
      expect(mockedDownloadSbom).toHaveBeenCalledWith(
        "proj-1",
        "cyclonedx-json",
        { scanId: "scan-old" },
      );
    });
  });

  it("renders the inline error alert when the API returns a ProblemError", async () => {
    mockedDownloadSbom.mockRejectedValue(
      new ProblemError("not ready", {
        status: 409,
        title: "scan not finished",
        detail: "No completed scan yet.",
        problem: null,
      }),
    );
    const user = userEvent.setup();
    render(<SbomTab projectId="proj-1" />);
    await user.click(screen.getByTestId("sbom-download-spdx-json"));
    await waitFor(() => {
      expect(screen.getByTestId("sbom-error")).toHaveTextContent(
        "No completed scan yet.",
      );
    });
  });

  // C3 — profile selector threads the chosen profile into the download.
  it("defaults to no profile so the canonical (signable) SBOM is exported", async () => {
    mockedDownloadSbom.mockResolvedValue({
      blob: new Blob(["{}"], { type: "application/json" }),
      filename: "sbom-proj-1.cdx.json",
      format: "cyclonedx-json",
    });
    const user = userEvent.setup();
    render(<SbomTab projectId="proj-1" />);
    // The default choice is pre-selected.
    expect(screen.getByTestId("sbom-profile-default")).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await user.click(screen.getByTestId("sbom-download-cyclonedx-json"));
    await waitFor(() => {
      expect(mockedDownloadSbom).toHaveBeenCalledWith("proj-1", "cyclonedx-json", {
        scanId: undefined,
        profile: undefined,
      });
    });
  });

  it("threads the selected policy profile into the download", async () => {
    mockedDownloadSbom.mockResolvedValue({
      blob: new Blob(["{}"], { type: "application/json" }),
      filename: "sbom-proj-1-policy-filtered.cdx.json",
      format: "cyclonedx-json",
    });
    const user = userEvent.setup();
    render(<SbomTab projectId="proj-1" />);
    await user.click(screen.getByTestId("sbom-profile-policy-filtered"));
    expect(screen.getByTestId("sbom-profile-policy-filtered")).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await user.click(screen.getByTestId("sbom-download-cyclonedx-json"));
    await waitFor(() => {
      expect(mockedDownloadSbom).toHaveBeenCalledWith("proj-1", "cyclonedx-json", {
        scanId: undefined,
        profile: "policy-filtered",
      });
    });
  });
});
