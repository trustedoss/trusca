/**
 * SandboxScanNotice — feat/demo-sandbox-scan.
 *
 * The explainer shown on the "Demo Sandbox" project: it must state the live
 * size cap, link out to BomLens for larger projects, and expose the SBOM
 * upload entry point.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SandboxScanNotice } from "@/features/projects/components/SandboxScanNotice";
import { DEMO_SANDBOX_SCAN_MAX_MB } from "@/lib/demoSandbox";

describe("SandboxScanNotice", () => {
  it("renders the live-scan size cap and a BomLens link", () => {
    render(<SandboxScanNotice onUploadSbom={vi.fn()} />);

    const notice = screen.getByTestId("sandbox-scan-notice");
    expect(notice).toHaveTextContent(String(DEMO_SANDBOX_SCAN_MAX_MB));
    expect(notice).toHaveTextContent(/BomLens/);

    const link = screen.getByTestId("sandbox-bomlens-link");
    expect(link).toHaveAttribute("href", expect.stringMatching(/^https?:\/\//));
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("warns that this is a shared public sandbox with visible, periodically-deleted uploads", () => {
    render(<SandboxScanNotice onUploadSbom={vi.fn()} />);

    const warning = screen.getByTestId("sandbox-shared-warning");
    expect(warning).toHaveTextContent(/shared, public sandbox/i);
    expect(warning).toHaveTextContent(/deleted periodically/i);
  });

  it("invokes onUploadSbom when the upload entry point is clicked", async () => {
    const onUploadSbom = vi.fn();
    render(<SandboxScanNotice onUploadSbom={onUploadSbom} />);

    await userEvent.click(screen.getByTestId("sandbox-upload-sbom"));
    expect(onUploadSbom).toHaveBeenCalledTimes(1);
  });
});
