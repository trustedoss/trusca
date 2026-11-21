/**
 * VexMenu — unit tests (v2.1 A3).
 *
 * Covers:
 *   - Two format items (OpenVEX / CycloneDX VEX) render and trigger the
 *     download with the right format.
 *   - A download error surfaces inline.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render as rtlRender, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VexMenu } from "@/features/projects/components/VexMenu";
import { ProblemError } from "@/lib/problem";

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

function render(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return rtlRender(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

const PROJECT_ID = "00000000-0000-0000-0000-projectid111";

describe("VexMenu", () => {
  beforeEach(() => {
    mockedDownload.mockReset();
    mockedTrigger.mockReset();
  });
  afterEach(() => vi.restoreAllMocks());

  async function openMenu(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByTestId("vex-menu-trigger"));
  }

  it("lists both VEX formats and the import item in the menu", async () => {
    const user = userEvent.setup();
    render(<VexMenu projectId={PROJECT_ID} projectName="demo" />);
    await openMenu(user);
    expect(screen.getByTestId("vex-import-open")).toBeInTheDocument();
    expect(screen.getByTestId("vex-export-openvex")).toBeInTheDocument();
    expect(screen.getByTestId("vex-export-cyclonedx")).toBeInTheDocument();
  });

  it("downloads OpenVEX with the right format and triggers the blob save", async () => {
    const user = userEvent.setup();
    mockedDownload.mockResolvedValueOnce({
      blob: new Blob(["{}"], { type: "application/json" }),
      filename: "demo-vex-openvex.json",
    });
    render(<VexMenu projectId={PROJECT_ID} projectName="demo" />);
    await openMenu(user);

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
    render(<VexMenu projectId={PROJECT_ID} projectName="demo" />);
    await openMenu(user);

    await user.click(screen.getByTestId("vex-export-cyclonedx"));
    await waitFor(() =>
      expect(mockedDownload).toHaveBeenCalledWith(
        PROJECT_ID,
        "cyclonedx",
        "demo",
      ),
    );
  });

  it("surfaces a download error inline, translated rather than the server's English", async () => {
    const user = userEvent.setup();
    mockedDownload.mockRejectedValueOnce(
      new ProblemError("database unavailable", {
        status: 500,
        title: "Internal Server Error",
        detail: "database unavailable",
        problem: null,
      }),
    );
    render(<VexMenu projectId={PROJECT_ID} projectName="demo" />);
    await openMenu(user);

    await user.click(screen.getByTestId("vex-export-openvex"));
    const err = await screen.findByTestId("vex-export-error");
    expect(err).toHaveTextContent("The VEX document could not be exported.");
    expect(err).toHaveTextContent("The server could not complete this.");
    expect(err).not.toHaveTextContent("database unavailable");
    expect(mockedTrigger).not.toHaveBeenCalled();
  });
});
