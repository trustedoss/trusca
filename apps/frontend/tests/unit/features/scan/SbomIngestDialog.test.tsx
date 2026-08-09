/**
 * SbomIngestDialog — feat/demo-sandbox-scan.
 *
 * Covers the upload lane the sandbox demo points visitors at for large
 * projects: pick an SBOM, upload it, and either open the progress drawer
 * (success) or see a localized RFC 7807 error. `sbomIngestErrorKey` maps every
 * status the endpoint documents.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: { post: vi.fn() },
}));

import {
  SbomIngestDialog,
  sbomIngestErrorKey,
} from "@/features/scan/SbomIngestDialog";
import { api } from "@/lib/api";
import { ProblemError } from "@/lib/problem";
import type { ProjectPublic } from "@/lib/projectsApi";

const mockedPost = vi.mocked(api.post);

function makeProject(): ProjectPublic {
  return {
    id: "proj-1",
    team_id: "team-1",
    name: "Demo Sandbox",
    slug: "demo-sandbox",
    description: null,
    git_url: null,
    default_branch: "main",
    declared_license: null,
    visibility: "team",
    archived_at: null,
    created_by_user_id: null,
    latest_scan_id: null,
    latest_scan_status: null,
    severity_summary: null,
    license_category_summary: null,
    created_by_user_name: null,
    has_git_credential: false,
    scan_count: 0,
    release_count: 0,
    last_scan_at: null,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
  };
}

function renderDialog(onIngestStarted = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <SbomIngestDialog
        open
        onOpenChange={vi.fn()}
        project={makeProject()}
        onIngestStarted={onIngestStarted}
      />
    </QueryClientProvider>,
  );
  return { onIngestStarted };
}

function problem(status: number): ProblemError {
  return new ProblemError("boom", {
    status,
    title: "t",
    detail: "d",
    problem: null,
  });
}

describe("sbomIngestErrorKey", () => {
  it("maps documented statuses to distinct i18n keys", () => {
    expect(sbomIngestErrorKey(problem(413))).toBe("sbom_ingest.errors.too_large");
    expect(sbomIngestErrorKey(problem(415))).toBe(
      "sbom_ingest.errors.unsupported_type",
    );
    expect(sbomIngestErrorKey(problem(422))).toBe(
      "sbom_ingest.errors.invalid_document",
    );
    expect(sbomIngestErrorKey(problem(409))).toBe(
      "sbom_ingest.errors.scan_in_progress",
    );
    expect(sbomIngestErrorKey(problem(404))).toBe("sbom_ingest.errors.not_found");
    expect(sbomIngestErrorKey(problem(429))).toBe(
      "sbom_ingest.errors.rate_limited",
    );
    expect(sbomIngestErrorKey(problem(0))).toBe("sbom_ingest.errors.network");
    expect(sbomIngestErrorKey(problem(500))).toBe("sbom_ingest.errors.unknown");
    expect(sbomIngestErrorKey(new Error("x"))).toBe("sbom_ingest.errors.unknown");
  });
});

describe("SbomIngestDialog", () => {
  beforeEach(() => mockedPost.mockReset());

  it("shows the shared public sandbox warning before upload", () => {
    renderDialog();
    const warning = screen.getByTestId("sbom-ingest-shared-warning");
    expect(warning).toHaveTextContent(/shared public sandbox/i);
    expect(warning).toHaveTextContent(/deleted periodically/i);
  });

  it("submit is disabled until a file is chosen, then enables", async () => {
    renderDialog();
    const submit = screen.getByTestId("sbom-ingest-submit");
    expect(submit).toBeDisabled();

    const input = screen.getByTestId("sbom-ingest-input");
    await userEvent.upload(
      input,
      new File(["{}"], "sbom.cdx.json", { type: "application/json" }),
    );

    expect(screen.getByTestId("sbom-ingest-selected")).toHaveTextContent(
      "sbom.cdx.json",
    );
    expect(submit).toBeEnabled();
  });

  it("uploads and hands the queued scan back to the parent", async () => {
    mockedPost.mockResolvedValueOnce({ data: { id: "scan-42", status: "queued" } });
    const { onIngestStarted } = renderDialog();

    await userEvent.upload(
      screen.getByTestId("sbom-ingest-input"),
      new File(["{}"], "sbom.cdx.json", { type: "application/json" }),
    );
    await userEvent.click(screen.getByTestId("sbom-ingest-submit"));

    await waitFor(() =>
      expect(onIngestStarted).toHaveBeenCalledWith(
        { id: "scan-42", status: "queued" },
        expect.objectContaining({ id: "proj-1" }),
      ),
    );
    expect(mockedPost).toHaveBeenCalledWith(
      "/v1/projects/proj-1/sbom-ingest",
      expect.any(FormData),
    );
  });

  it("accepts an optional release label and cancels without uploading", async () => {
    const onOpenChange = vi.fn();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <SbomIngestDialog
          open
          onOpenChange={onOpenChange}
          project={makeProject()}
          onIngestStarted={vi.fn()}
        />
      </QueryClientProvider>,
    );

    await userEvent.type(
      screen.getByTestId("sbom-ingest-release"),
      "v2.0.0",
    );
    expect(screen.getByTestId("sbom-ingest-release")).toHaveValue("v2.0.0");

    await userEvent.click(screen.getByTestId("sbom-ingest-cancel"));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(mockedPost).not.toHaveBeenCalled();
  });

  it("shows a localized error when the server rejects the SBOM (422)", async () => {
    mockedPost.mockRejectedValueOnce(problem(422));
    renderDialog();

    await userEvent.upload(
      screen.getByTestId("sbom-ingest-input"),
      new File(["not sbom"], "bad.json", { type: "application/json" }),
    );
    await userEvent.click(screen.getByTestId("sbom-ingest-submit"));

    const err = await screen.findByTestId("sbom-ingest-error");
    expect(err).toHaveTextContent(/CycloneDX or SPDX SBOM/i);
  });
});
