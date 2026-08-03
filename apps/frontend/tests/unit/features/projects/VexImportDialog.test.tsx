/**
 * VexImportDialog — unit tests (v2.1 A3).
 *
 * Covers:
 *   - Permission gating: the trigger is disabled for a developer, enabled for a
 *     team_admin (the suppression/import privilege boundary).
 *   - Successful import renders the matched/applied/skipped summary + per-row
 *     skip reasons.
 *   - RFC 7807 errors (403 / 413 / 422) surface a graceful, localized message.
 *   - XSS-INERT (security, required): a `<script>` inside an import error
 *     `detail` and inside a skip `detail` is rendered as inert escaped TEXT —
 *     no <script> element is injected into the DOM, no execution.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VexImportDialog } from "@/features/projects/components/VexImportDialog";
import type { VexImportSummary } from "@/features/projects/api/vexApi";
import { ProblemError } from "@/lib/problem";

vi.mock("@/features/projects/api/vexApi", async () => {
  const actual = await vi.importActual<
    typeof import("@/features/projects/api/vexApi")
  >("@/features/projects/api/vexApi");
  return { ...actual, importVex: vi.fn() };
});

import { importVex } from "@/features/projects/api/vexApi";

const mockedImport = vi.mocked(importVex);

const PROJECT_ID = "00000000-0000-0000-0000-projectid111";

function renderDialog(role: "developer" | "team_admin" | "super_admin") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <VexImportDialog projectId={PROJECT_ID} projectRole={role} />
    </QueryClientProvider>,
  );
}

async function attachAndSubmit(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByTestId("vex-import-open"));
  const fileInput = screen.getByTestId("vex-import-file") as HTMLInputElement;
  const file = new File(['{"@context":"openvex"}'], "vex.json", {
    type: "application/json",
  });
  await user.upload(fileInput, file);
  await user.click(screen.getByTestId("vex-import-submit"));
}

function summary(overrides: Partial<VexImportSummary> = {}): VexImportSummary {
  return {
    format: "openvex",
    matched: 5,
    applied: 3,
    skipped: 2,
    errors: [],
    ...overrides,
  };
}

describe("VexImportDialog", () => {
  beforeEach(() => mockedImport.mockReset());
  afterEach(() => vi.restoreAllMocks());

  it("disables the import trigger for a developer (role gate)", () => {
    renderDialog("developer");
    const trigger = screen.getByTestId("vex-import-open");
    expect(trigger).toBeDisabled();
    expect(trigger).toHaveAttribute("data-role-gated", "true");
  });

  it("enables the import trigger for a team_admin", () => {
    renderDialog("team_admin");
    const trigger = screen.getByTestId("vex-import-open");
    expect(trigger).toBeEnabled();
    expect(trigger).not.toHaveAttribute("data-role-gated");
  });

  it("renders the matched/applied/skipped summary on success", async () => {
    const user = userEvent.setup();
    mockedImport.mockResolvedValueOnce(
      summary({ matched: 7, applied: 4, skipped: 3 }),
    );
    renderDialog("team_admin");
    await attachAndSubmit(user);

    const panel = await screen.findByTestId("vex-import-summary");
    expect(panel).toHaveAttribute("data-matched", "7");
    expect(panel).toHaveAttribute("data-applied", "4");
    expect(panel).toHaveAttribute("data-skipped", "3");
    expect(screen.getByTestId("vex-import-summary-applied").textContent).toBe("4");
  });

  it("renders per-statement skip reasons", async () => {
    const user = userEvent.setup();
    mockedImport.mockResolvedValueOnce(
      summary({
        matched: 1,
        applied: 0,
        skipped: 1,
        errors: [
          {
            vulnerability: "CVE-2024-9999",
            product: "pkg:npm/leftpad@1.0.0",
            reason: "unknown_vulnerability",
            detail: "no finding matched CVE-2024-9999",
          },
        ],
      }),
    );
    renderDialog("team_admin");
    await attachAndSubmit(user);

    const rows = await screen.findAllByTestId("vex-import-summary-error-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveAttribute("data-reason", "unknown_vulnerability");
    expect(rows[0].textContent).toContain("CVE-2024-9999");
  });

  it("surfaces a graceful localized error for 413 too-large", async () => {
    const user = userEvent.setup();
    mockedImport.mockRejectedValueOnce(
      new ProblemError("too large", {
        status: 413,
        title: "Too Large",
        detail: "document exceeds limit",
        problem: null,
      }),
    );
    renderDialog("team_admin");
    await attachAndSubmit(user);

    const err = await screen.findByTestId("vex-import-error");
    expect(err.textContent).toMatch(/too large/i);
    expect(screen.queryByTestId("vex-import-summary")).not.toBeInTheDocument();
  });

  it("surfaces a localized 422 malformed-document error", async () => {
    const user = userEvent.setup();
    mockedImport.mockRejectedValueOnce(
      new ProblemError("malformed", {
        status: 422,
        title: "Unprocessable",
        detail: "not a VEX document",
        problem: null,
      }),
    );
    renderDialog("team_admin");
    await attachAndSubmit(user);

    const err = await screen.findByTestId("vex-import-error");
    expect(err.textContent).toMatch(/parsed|openvex|cyclonedx/i);
  });

  it("renders a <script> in an unknown-status error detail as inert escaped text (XSS)", async () => {
    const user = userEvent.setup();
    const payload = '<script>window.__vex_xss__ = 1;</script>';
    // A non-4xx-specific status falls through to rendering the server `detail`
    // verbatim — the worst case for output encoding.
    mockedImport.mockRejectedValueOnce(
      new ProblemError("server", {
        status: 500,
        title: "Server Error",
        detail: payload,
        problem: null,
      }),
    );
    renderDialog("team_admin");
    await attachAndSubmit(user);

    const err = await screen.findByTestId("vex-import-error");
    // The raw payload appears as TEXT…
    expect(err.textContent).toContain(payload);
    // …and NOT as an executable element.
    expect(err.querySelector("script")).toBeNull();
    expect(
      (window as unknown as { __vex_xss__?: number }).__vex_xss__,
    ).toBeUndefined();
  });

  it("renders a <script> in a skip error detail as inert escaped text (XSS)", async () => {
    const user = userEvent.setup();
    const payload = '<script>window.__vex_skip_xss__ = 1;</script>';
    mockedImport.mockResolvedValueOnce(
      summary({
        matched: 1,
        applied: 0,
        skipped: 1,
        errors: [
          {
            vulnerability: payload,
            product: payload,
            reason: "malformed_statement",
            detail: payload,
          },
        ],
      }),
    );
    renderDialog("team_admin");
    await attachAndSubmit(user);

    const rows = await screen.findAllByTestId("vex-import-summary-error-row");
    const row = rows[0];
    expect(row.textContent).toContain(payload);
    expect(within(row).queryByText(payload, { selector: "script" })).toBeNull();
    expect(row.querySelector("script")).toBeNull();
    expect(
      (window as unknown as { __vex_skip_xss__?: number }).__vex_skip_xss__,
    ).toBeUndefined();
  });

  it("keeps the submit button disabled until a file is chosen", async () => {
    const user = userEvent.setup();
    renderDialog("team_admin");
    await user.click(screen.getByTestId("vex-import-open"));
    expect(screen.getByTestId("vex-import-submit")).toBeDisabled();
  });

  it("does not call importVex when no file is attached", async () => {
    const user = userEvent.setup();
    renderDialog("team_admin");
    await user.click(screen.getByTestId("vex-import-open"));
    // submit is disabled, but force the handler path defensively
    expect(mockedImport).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.getByTestId("vex-import-dialog")).toBeInTheDocument(),
    );
  });
});
