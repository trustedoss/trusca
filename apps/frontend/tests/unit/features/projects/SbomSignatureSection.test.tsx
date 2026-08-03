/**
 * SbomSignatureSection — unit tests (v2.3-s3).
 *
 * Coverage targets:
 *   - Primary bundle button + secondary artifact buttons render with testids.
 *   - Clicking the bundle button fetches the `bundle` artifact and hands the
 *     blob to the browser (URL.createObjectURL proxy).
 *   - Clicking a secondary button fetches the matching artifact.
 *   - A 404 on the bundle (unsigned scan) flips to the calm "unsigned" state,
 *     NOT a destructive error.
 *   - A 404 on a certificate artifact (key-based deployment) shows the quiet
 *     "not applicable" hint, NOT an error alert.
 *   - A non-404 failure surfaces the inline error alert.
 *   - The verification docs link is present and opens in a new tab safely.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SbomSignatureSection } from "@/features/projects/components/SbomSignatureSection";
import { ProblemError } from "@/lib/problem";

vi.mock("@/lib/projectsApi", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/projectsApi")>(
      "@/lib/projectsApi",
    );
  return {
    ...actual,
    downloadSbomSignatureArtifact: vi.fn(),
  };
});

import { downloadSbomSignatureArtifact } from "@/lib/projectsApi";
const mockedDownload = vi.mocked(downloadSbomSignatureArtifact);

function notFound(detail: string): ProblemError {
  return new ProblemError(detail, {
    status: 404,
    title: "Not Found",
    detail,
    problem: null,
  });
}

describe("SbomSignatureSection", () => {
  let originalCreateObjectURL: unknown;
  let originalRevokeObjectURL: unknown;

  beforeEach(() => {
    mockedDownload.mockReset();
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

  function createObjectURLCalls(): number {
    return (
      URL as unknown as { createObjectURL: { mock: { calls: unknown[] } } }
    ).createObjectURL.mock.calls.length;
  }

  it("renders the primary bundle button, secondary artifacts, and verify link", () => {
    render(<SbomSignatureSection projectId="proj-1" />);
    expect(
      screen.getByTestId("sbom-signature-section"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("sbom-signature-download-bundle"),
    ).toBeInTheDocument();
    for (const suffix of [
      "signature",
      "public-key",
      "attestation",
      "certificate",
      "attestation-certificate",
    ]) {
      expect(
        screen.getByTestId(`sbom-signature-download-${suffix}`),
      ).toBeInTheDocument();
    }
    const link = screen.getByTestId("sbom-signature-verify-docs");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("downloads the bundle and hands the blob to the browser", async () => {
    mockedDownload.mockResolvedValue({
      blob: new Blob(["zip"], { type: "application/zip" }),
      filename: "sbom-signature-acme.zip",
      artifact: "bundle",
    });
    const user = userEvent.setup();
    render(<SbomSignatureSection projectId="proj-1" />);
    await user.click(screen.getByTestId("sbom-signature-download-bundle"));
    await waitFor(() => {
      expect(mockedDownload).toHaveBeenCalledWith("proj-1", "bundle");
    });
    expect(createObjectURLCalls()).toBeGreaterThanOrEqual(1);
    // No error / unsigned state on success.
    expect(
      screen.queryByTestId("sbom-signature-error"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("sbom-signature-unsigned"),
    ).not.toBeInTheDocument();
  });

  it("downloads an individual artifact (public key) when its button is clicked", async () => {
    mockedDownload.mockResolvedValue({
      blob: new Blob(["-----BEGIN PUBLIC KEY-----"], {
        type: "application/x-pem-file",
      }),
      filename: "cosign.pub",
      artifact: "public-key",
    });
    const user = userEvent.setup();
    render(<SbomSignatureSection projectId="proj-1" />);
    await user.click(screen.getByTestId("sbom-signature-download-public-key"));
    await waitFor(() => {
      expect(mockedDownload).toHaveBeenCalledWith("proj-1", "public-key");
    });
    expect(createObjectURLCalls()).toBeGreaterThanOrEqual(1);
  });

  it("flips to the calm 'unsigned' state when the bundle 404s (no signature)", async () => {
    mockedDownload.mockRejectedValue(notFound("No signed SBOM is available."));
    const user = userEvent.setup();
    render(<SbomSignatureSection projectId="proj-1" />);
    await user.click(screen.getByTestId("sbom-signature-download-bundle"));
    await waitFor(() => {
      expect(
        screen.getByTestId("sbom-signature-unsigned"),
      ).toBeInTheDocument();
    });
    // Unsigned is NOT a destructive error.
    expect(
      screen.queryByTestId("sbom-signature-error"),
    ).not.toBeInTheDocument();
  });

  it("shows the 'not applicable' hint when a certificate 404s on key-based signing", async () => {
    mockedDownload.mockRejectedValue(
      notFound("No signing certificate is available."),
    );
    const user = userEvent.setup();
    render(<SbomSignatureSection projectId="proj-1" />);
    await user.click(screen.getByTestId("sbom-signature-download-certificate"));
    await waitFor(() => {
      expect(
        screen.getByTestId("sbom-signature-not-applicable"),
      ).toBeInTheDocument();
    });
    // A certificate 404 is an expected branch, not an error alert.
    expect(
      screen.queryByTestId("sbom-signature-error"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("sbom-signature-unsigned"),
    ).not.toBeInTheDocument();
  });

  it("surfaces an inline error alert for a non-404 failure", async () => {
    mockedDownload.mockRejectedValue(
      new ProblemError("boom", {
        status: 500,
        title: "Internal Server Error",
        detail: "Something went wrong assembling the bundle.",
        problem: null,
      }),
    );
    const user = userEvent.setup();
    render(<SbomSignatureSection projectId="proj-1" />);
    await user.click(screen.getByTestId("sbom-signature-download-bundle"));
    await waitFor(() => {
      expect(screen.getByTestId("sbom-signature-error")).toHaveTextContent(
        "Something went wrong assembling the bundle.",
      );
    });
    // A 500 is not the unsigned branch.
    expect(
      screen.queryByTestId("sbom-signature-unsigned"),
    ).not.toBeInTheDocument();
  });

  it("disables every button while a download is in flight", async () => {
    const deferred: {
      resolve?: (value: Awaited<ReturnType<typeof mockedDownload>>) => void;
    } = {};
    mockedDownload.mockImplementation(
      () =>
        new Promise((resolve) => {
          deferred.resolve = resolve;
        }),
    );
    const user = userEvent.setup();
    render(<SbomSignatureSection projectId="proj-1" />);
    await user.click(screen.getByTestId("sbom-signature-download-bundle"));
    await waitFor(() => {
      expect(
        screen.getByTestId("sbom-signature-download-signature"),
      ).toBeDisabled();
    });
    expect(deferred.resolve).toBeDefined();
    deferred.resolve?.({
      blob: new Blob(["zip"], { type: "application/zip" }),
      filename: "sbom-signature-acme.zip",
      artifact: "bundle",
    });
    await waitFor(() => {
      expect(
        screen.getByTestId("sbom-signature-download-signature"),
      ).not.toBeDisabled();
    });
  });
});
