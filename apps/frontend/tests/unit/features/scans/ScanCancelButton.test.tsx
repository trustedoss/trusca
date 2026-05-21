/**
 * ScanCancelButton — unit tests (PR-A3).
 *
 * Coverage targets:
 *   - Renders the trigger only for queued/running scans (hidden for terminal).
 *   - Trigger → inline confirm strip (no full-page nav, no modal AlertDialog).
 *   - Confirm dispatches `POST /v1/scans/{id}/cancel` and fires `onCancelled`.
 *   - Dismiss closes the strip without calling the API.
 *   - 409 `scan_already_cancelled` surfaces a toast keyed for e2e assertion.
 *   - 404 `scan_not_found` and 403 forbidden each map to their own toast key.
 *
 * The wire layer (`cancelScan`) is mocked so no backend is needed; i18n is the
 * real EN bundle (loaded by tests/setup.ts) so we assert on rendered copy too.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ScanCancelButton } from "@/features/scans/ScanCancelButton";
import { ProblemError } from "@/lib/problem";
import type { ScanPublic, ScanStatus } from "@/lib/projectsApi";

vi.mock("@/lib/projectsApi", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/projectsApi")>(
      "@/lib/projectsApi",
    );
  return { ...actual, cancelScan: vi.fn() };
});

import { cancelScan } from "@/lib/projectsApi";
const mockedCancel = vi.mocked(cancelScan);

const SCAN_ID = "11111111-1111-1111-1111-111111111111";

function scanFixture(status: ScanStatus): ScanPublic {
  return {
    id: SCAN_ID,
    project_id: "abcdef12-3456-7890-abcd-ef1234567890",
    kind: "source",
    status,
    progress_percent: 50,
    current_step: null,
    started_at: "2026-05-08T00:00:00Z",
    completed_at: null,
    error_message: null,
    requested_by_user_id: null,
    celery_task_id: null,
    metadata: {},
    created_at: "2026-05-08T00:00:00Z",
    updated_at: "2026-05-08T00:00:00Z",
  };
}

function problem(
  status: number,
  extension?: Record<string, unknown>,
): ProblemError {
  return new ProblemError("cancel failed", {
    status,
    title: "Conflict",
    detail: "nope",
    problem: {
      type: "about:blank",
      title: "Conflict",
      status,
      detail: "nope",
      ...extension,
    },
  });
}

function renderButton(
  status: ScanStatus,
  onCancelled = vi.fn(),
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ScanCancelButton scanId={SCAN_ID} status={status} onCancelled={onCancelled} />
    </QueryClientProvider>,
  );
  return { onCancelled };
}

describe("ScanCancelButton", () => {
  beforeEach(() => {
    mockedCancel.mockReset();
  });

  it("renders the trigger for a running scan and hides it for terminal scans", () => {
    const { unmount } = render(
      <QueryClientProvider client={new QueryClient()}>
        <ScanCancelButton scanId={SCAN_ID} status="running" />
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("scan-cancel-button")).toBeInTheDocument();
    unmount();

    render(
      <QueryClientProvider client={new QueryClient()}>
        <ScanCancelButton scanId={SCAN_ID} status="succeeded" />
      </QueryClientProvider>,
    );
    expect(screen.queryByTestId("scan-cancel-button")).not.toBeInTheDocument();
  });

  it("opens an inline confirm strip and dispatches cancel on confirm", async () => {
    mockedCancel.mockResolvedValue(scanFixture("cancelled"));
    const { onCancelled } = renderButton("queued");

    await userEvent.click(screen.getByTestId("scan-cancel-button"));
    expect(screen.getByTestId("scan-cancel-confirm")).toBeInTheDocument();

    await userEvent.click(screen.getByTestId("scan-cancel-confirm-ok"));
    await waitFor(() => {
      expect(mockedCancel).toHaveBeenCalledWith(SCAN_ID);
    });
    expect(onCancelled).toHaveBeenCalledTimes(1);
  });

  it("dismiss closes the confirm strip without calling the API", async () => {
    renderButton("running");
    await userEvent.click(screen.getByTestId("scan-cancel-button"));
    await userEvent.click(screen.getByTestId("scan-cancel-dismiss"));
    expect(screen.queryByTestId("scan-cancel-confirm")).not.toBeInTheDocument();
    expect(screen.getByTestId("scan-cancel-button")).toBeInTheDocument();
    expect(mockedCancel).not.toHaveBeenCalled();
  });

  it("surfaces an already-cancelled toast on a 409 with scan_already_cancelled", async () => {
    mockedCancel.mockRejectedValue(
      problem(409, { scan_already_cancelled: true }),
    );
    renderButton("running");
    await userEvent.click(screen.getByTestId("scan-cancel-button"));
    await userEvent.click(screen.getByTestId("scan-cancel-confirm-ok"));

    const toast = await screen.findByTestId("scan-cancel-toast");
    expect(toast).toHaveAttribute("data-toast-key", "scan_already_cancelled");
    expect(toast).toHaveTextContent(/already finished/i);
  });

  it("maps a 404 scan_not_found to the not-found toast key", async () => {
    mockedCancel.mockRejectedValue(problem(404, { scan_not_found: true }));
    renderButton("running");
    await userEvent.click(screen.getByTestId("scan-cancel-button"));
    await userEvent.click(screen.getByTestId("scan-cancel-confirm-ok"));

    const toast = await screen.findByTestId("scan-cancel-toast");
    expect(toast).toHaveAttribute("data-toast-key", "scan_not_found");
  });

  it("maps a 403 to the forbidden toast key", async () => {
    mockedCancel.mockRejectedValue(problem(403));
    renderButton("running");
    await userEvent.click(screen.getByTestId("scan-cancel-button"));
    await userEvent.click(screen.getByTestId("scan-cancel-confirm-ok"));

    const toast = await screen.findByTestId("scan-cancel-toast");
    expect(toast).toHaveAttribute("data-toast-key", "forbidden");
  });
});
