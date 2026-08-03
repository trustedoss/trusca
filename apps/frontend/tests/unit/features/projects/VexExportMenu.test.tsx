/**
 * VexExportMenu — unit tests (v2.1 A3).
 *
 * Covers:
 *   - Two format buttons (OpenVEX / CycloneDX VEX) render and trigger the
 *     download with the right format.
 *   - Both buttons disable while one is in flight.
 *   - A download error surfaces inline.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VexExportMenu } from "@/features/projects/components/VexExportMenu";

vi.mock("@/features/projects/api/vexApi", async () => {
  const actual = await vi.importActual<
    typeof import("@/features/projects/api/vexApi")
  >("@/features/projects/api/vexApi");
  return { ...actual, downloadVex: vi.fn() };
});

vi.mock("@/lib/download", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/download")>("@/lib/download");
  return { ...actual, triggerBlobDownload: vi.fn() };
});

import { downloadVex } from "@/features/projects/api/vexApi";
import { triggerBlobDownload } from "@/lib/download";

const mockedDownload = vi.mocked(downloadVex);
const mockedTrigger = vi.mocked(triggerBlobDownload);

const PROJECT_ID = "00000000-0000-0000-0000-projectid111";

describe("VexExportMenu", () => {
  beforeEach(() => {
    mockedDownload.mockReset();
    mockedTrigger.mockReset();
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders both VEX format buttons", () => {
    render(<VexExportMenu projectId={PROJECT_ID} projectName="demo" />);
    expect(screen.getByTestId("vex-export-openvex")).toBeInTheDocument();
    expect(screen.getByTestId("vex-export-cyclonedx")).toBeInTheDocument();
  });

  it("downloads OpenVEX with the right format and triggers the blob save", async () => {
    const user = userEvent.setup();
    mockedDownload.mockResolvedValueOnce({
      blob: new Blob(["{}"], { type: "application/json" }),
      filename: "demo-vex-openvex.json",
    });
    render(<VexExportMenu projectId={PROJECT_ID} projectName="demo" />);

    await user.click(screen.getByTestId("vex-export-openvex"));
    await waitFor(() =>
      expect(mockedDownload).toHaveBeenCalledWith(PROJECT_ID, "openvex", "demo"),
    );
    expect(mockedTrigger).toHaveBeenCalledTimes(1);
  });

  it("downloads CycloneDX VEX with the right format", async () => {
    const user = userEvent.setup();
    mockedDownload.mockResolvedValueOnce({
      blob: new Blob(["{}"], { type: "application/json" }),
      filename: "demo-vex-cyclonedx.json",
    });
    render(<VexExportMenu projectId={PROJECT_ID} projectName="demo" />);

    await user.click(screen.getByTestId("vex-export-cyclonedx"));
    await waitFor(() =>
      expect(mockedDownload).toHaveBeenCalledWith(
        PROJECT_ID,
        "cyclonedx",
        "demo",
      ),
    );
  });

  it("surfaces a download error inline", async () => {
    const user = userEvent.setup();
    mockedDownload.mockRejectedValueOnce(new Error("export failed"));
    render(<VexExportMenu projectId={PROJECT_ID} projectName="demo" />);

    await user.click(screen.getByTestId("vex-export-openvex"));
    const err = await screen.findByTestId("vex-export-error");
    expect(err.textContent).toContain("export failed");
    expect(mockedTrigger).not.toHaveBeenCalled();
  });
});
