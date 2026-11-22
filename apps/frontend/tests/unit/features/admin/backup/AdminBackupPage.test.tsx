/**
 * AdminBackupPage — Phase 6 PR #19 chore D unit tests.
 *
 * We mock the wire layer so the page can render without a backend. The
 * tests assert the operator-facing invariants:
 *
 *   1. List rows render once the query resolves; auto vs manual rows
 *      surface the correct kind badge.
 *   2. The "Run manual backup now" button calls the POST endpoint.
 *   3. Delete is two-step: the first click only opens the inline confirm
 *      strip; the destructive call fires only after the second click.
 *   4. Picking a restore file opens a confirmation dialog. Its danger
 *      button is disabled until the user types `restore` exactly
 *      (case-sensitive); cancelling closes the dialog without uploading.
 *   5. Auto rows render the Delete control as disabled (the backend
 *      returns 409 for `auto-*` and the UI surfaces the rule up front).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AdminBackupPage } from "@/features/admin/backup/AdminBackupPage";
import { ProblemError, type ProblemDetails } from "@/lib/problem";
import type {
  BackupInfo,
  BackupListResponse,
} from "@/features/admin/api/adminBackupsApi";

vi.mock("@/features/admin/api/adminBackupsApi", () => {
  return {
    listBackups: vi.fn(),
    triggerManualBackup: vi.fn(),
    downloadBackup: vi.fn(),
    deleteBackup: vi.fn(),
    uploadRestore: vi.fn(),
  };
});

import {
  deleteBackup,
  downloadBackup,
  listBackups,
  triggerManualBackup,
  uploadRestore,
} from "@/features/admin/api/adminBackupsApi";

const mockedList = vi.mocked(listBackups);
const mockedTrigger = vi.mocked(triggerManualBackup);
const mockedDelete = vi.mocked(deleteBackup);
const mockedUpload = vi.mocked(uploadRestore);
const mockedDownload = vi.mocked(downloadBackup);

function backup(overrides: Partial<BackupInfo>): BackupInfo {
  return {
    name: overrides.name ?? "manual-20260509T120000Z",
    kind: overrides.kind ?? "manual",
    created_at: overrides.created_at ?? "2026-05-09T12:00:00Z",
    size_bytes: overrides.size_bytes ?? 1024,
    db_revision: overrides.db_revision ?? "abcdef1234567",
    complete: overrides.complete ?? true,
  };
}

function listResponse(items: BackupInfo[]): BackupListResponse {
  return { items, total: items.length };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AdminBackupPage />
    </QueryClientProvider>,
  );
}

describe("AdminBackupPage", () => {
  beforeEach(() => {
    mockedList.mockReset();
    mockedTrigger.mockReset();
    mockedDelete.mockReset();
    mockedUpload.mockReset();
    mockedDownload.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders rows once the list query resolves", async () => {
    mockedList.mockResolvedValue(
      listResponse([
        backup({ name: "auto-20260508T000000Z", kind: "auto" }),
        backup({ name: "manual-20260509T120000Z", kind: "manual" }),
      ]),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByTestId("admin-backup-row")).toHaveLength(2);
    });
    const badges = screen.getAllByTestId("admin-backup-kind-badge");
    expect(badges[0]).toBeInTheDocument();
    // Auto badge first (sorted as returned by mock).
    const firstRow = screen.getAllByTestId("admin-backup-row")[0];
    expect(firstRow.getAttribute("data-kind")).toBe("auto");
  });

  it("replaces the table with an empty state when the list is empty", async () => {
    mockedList.mockResolvedValue(listResponse([]));
    renderPage();
    const empty = await screen.findByTestId("admin-backup-empty");
    expect(empty).toHaveTextContent("No backups yet");
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByTestId("admin-backup-table")).toHaveAttribute(
      "aria-busy",
      "false",
    );
  });

  it("puts Run manual backup in the page header as the one primary button", async () => {
    mockedList.mockResolvedValue(listResponse([]));
    renderPage();
    const trigger = screen.getByTestId("admin-backup-manual-trigger");
    const header = trigger.closest("header");
    expect(header).not.toBeNull();
    const variants = Array.from(
      (header as HTMLElement).querySelectorAll("button"),
      (b) => b.getAttribute("data-variant"),
    );
    expect(variants.filter((v) => v === "primary")).toHaveLength(1);
    expect(trigger.getAttribute("data-variant")).toBe("primary");
    expect(
      within(header as HTMLElement).getByTestId("admin-backup-file-picker"),
    ).toBeInTheDocument();
  });

  it("Run manual backup → calls triggerManualBackup", async () => {
    mockedList.mockResolvedValue(listResponse([]));
    mockedTrigger.mockResolvedValue({
      task_id: "t-1",
      name: "manual-x",
    });
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-backup-manual-trigger")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("admin-backup-manual-trigger"));
    await waitFor(() => {
      expect(mockedTrigger).toHaveBeenCalledTimes(1);
    });
  });

  it("Delete is two-step: first click opens the confirm strip, second deletes", async () => {
    const row = backup({ name: "manual-keep", kind: "manual" });
    mockedList.mockResolvedValue(listResponse([row]));
    mockedDelete.mockResolvedValue();
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-backup-row")).toBeInTheDocument();
    });
    // Initial state: no confirm strip.
    expect(
      screen.queryByTestId("admin-backup-confirm-strip"),
    ).not.toBeInTheDocument();
    // First click — open the strip; mutation must NOT fire yet.
    await userEvent.click(screen.getByTestId("admin-backup-action-delete"));
    expect(screen.getByTestId("admin-backup-confirm-strip")).toBeInTheDocument();
    expect(mockedDelete).not.toHaveBeenCalled();
    // Second click on the confirm OK — the destructive call fires.
    await userEvent.click(screen.getByTestId("admin-backup-confirm-ok"));
    await waitFor(() => {
      expect(mockedDelete).toHaveBeenCalledWith("manual-keep");
    });
  });

  it("Auto rows render Delete as disabled with a tooltip — destructive call gated", async () => {
    const auto = backup({ name: "auto-pruned", kind: "auto" });
    mockedList.mockResolvedValue(listResponse([auto]));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-backup-row")).toBeInTheDocument();
    });
    const disabled = screen.getByTestId("admin-backup-action-delete-disabled");
    expect(disabled).toBeDisabled();
    // No active "delete" action button is rendered for auto rows.
    expect(
      screen.queryByTestId("admin-backup-action-delete"),
    ).not.toBeInTheDocument();
  });

  it("Restore button is gated by the typing confirmation (case-sensitive)", async () => {
    mockedList.mockResolvedValue(listResponse([]));
    mockedUpload.mockResolvedValue({ task_id: "r-1", message: "queued" });
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-backup-file-picker")).toBeInTheDocument();
    });
    // Inject a file via the hidden input — userEvent.upload bypasses the
    // visible "Upload and restore…" Button so we can drive the input
    // element directly.
    const input = screen.getByTestId(
      "admin-backup-file-input",
    ) as HTMLInputElement;
    const file = new File(["payload"], "snap.tar.gz", {
      type: "application/gzip",
    });
    expect(screen.queryByRole("dialog")).toBeNull();
    await userEvent.upload(input, file);
    // The confirmation is a modal dialog holding the warning, the file name
    // and the typing gate, with a danger button.
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByTestId("admin-backup-restore-warning"),
    ).toHaveTextContent("overwrite ALL data");
    expect(within(dialog).getByTestId("admin-backup-file-name")).toHaveTextContent(
      "snap.tar.gz",
    );
    const submit = within(dialog).getByTestId("admin-backup-restore-submit");
    expect(submit.getAttribute("data-variant")).toBe("danger");
    expect(submit).toBeDisabled();
    // Wrong text — case-sensitive — must keep the button disabled.
    const confirmInput = screen.getByTestId("admin-backup-restore-confirm");
    await userEvent.type(confirmInput, "Restore");
    expect(submit).toBeDisabled();
    // Clear + type the exact token.
    await userEvent.clear(confirmInput);
    await userEvent.type(confirmInput, "restore");
    await waitFor(() => {
      expect(submit).not.toBeDisabled();
    });
    await userEvent.click(submit);
    await waitFor(() => {
      expect(mockedUpload).toHaveBeenCalledTimes(1);
    });
    expect(mockedUpload.mock.calls[0]?.[0]).toBeInstanceOf(File);
    // Persistent banner appears after success, and the dialog closes.
    await waitFor(() => {
      expect(screen.getByTestId("admin-backup-restore-queued")).toBeInTheDocument();
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("Cancel closes the restore dialog without uploading, and the same file can be picked again", async () => {
    mockedList.mockResolvedValue(listResponse([]));
    renderPage();
    const input = screen.getByTestId(
      "admin-backup-file-input",
    ) as HTMLInputElement;
    const file = new File(["payload"], "snap.tar.gz", {
      type: "application/gzip",
    });
    await userEvent.upload(input, file);
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(
      within(dialog).getByTestId("admin-backup-restore-confirm"),
      "restore",
    );
    await userEvent.click(within(dialog).getByTestId("admin-backup-restore-cancel"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(input.value).toBe("");
    expect(mockedUpload).not.toHaveBeenCalled();

    // Reopening starts from an empty gate, not the token typed before.
    await userEvent.upload(input, file);
    const reopened = await screen.findByRole("dialog");
    expect(within(reopened).getByTestId("admin-backup-restore-confirm")).toHaveValue("");
    expect(within(reopened).getByTestId("admin-backup-restore-submit")).toBeDisabled();
  });

  it("Escape closes the restore dialog without uploading", async () => {
    mockedList.mockResolvedValue(listResponse([]));
    renderPage();
    const input = screen.getByTestId(
      "admin-backup-file-input",
    ) as HTMLInputElement;
    await userEvent.upload(
      input,
      new File(["payload"], "snap.tar.gz", { type: "application/gzip" }),
    );
    await screen.findByRole("dialog");
    await userEvent.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(mockedUpload).not.toHaveBeenCalled();
  });

  it("Download triggers downloadBackup with the row's name", async () => {
    const row = backup({ name: "manual-dl", kind: "manual" });
    mockedList.mockResolvedValue(listResponse([row]));
    mockedDownload.mockResolvedValue();
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-backup-row")).toBeInTheDocument();
    });
    const tr = screen.getByTestId("admin-backup-row");
    await userEvent.click(
      within(tr).getByTestId("admin-backup-action-download"),
    );
    await waitFor(() => {
      expect(mockedDownload).toHaveBeenCalledWith("manual-dl");
    });
  });

  it("Marks a backup whose run did not finish, and leaves finished ones unmarked", async () => {
    // Both rows together, because a badge that appears on every row says as
    // little as one that appears on none: what the operator reads is the
    // difference between the two.
    const finished = backup({ name: "manual-ok", complete: true });
    const killed = backup({ name: "auto-killed", kind: "auto", complete: false });
    mockedList.mockResolvedValue(listResponse([finished, killed]));
    renderPage();

    await waitFor(() => {
      expect(screen.getAllByTestId("admin-backup-row")).toHaveLength(2);
    });

    const rows = screen.getAllByTestId("admin-backup-row");
    const finishedRow = rows.find((r) => within(r).queryByText("manual-ok"));
    const killedRow = rows.find((r) => within(r).queryByText("auto-killed"));

    expect(
      within(killedRow as HTMLElement).getByTestId("admin-backup-incomplete-badge"),
    ).toBeInTheDocument();
    expect(
      within(finishedRow as HTMLElement).queryByTestId("admin-backup-incomplete-badge"),
    ).toBeNull();
  });

  it("Explains the mark in words, not by colour alone", async () => {
    // The badge is the only thing standing between an operator and counting a
    // restore point that will not restore, so it has to say what it means:
    // colour is not a signal on its own (design system, A11y).
    const killed = backup({ name: "auto-killed", kind: "auto", complete: false });
    mockedList.mockResolvedValue(listResponse([killed]));
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("admin-backup-incomplete-badge")).toBeInTheDocument();
    });

    const badge = screen.getByTestId("admin-backup-incomplete-badge");
    expect(badge.textContent?.trim()).not.toHaveLength(0);
    expect(badge.getAttribute("title")).toBeTruthy();
  });

  it("Renders an error alert when the list query fails", async () => {
    mockedList.mockRejectedValue(new Error("boom"));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-backup-error")).toBeInTheDocument();
    });
  });

  // The public demo hides this screen (backend demo middleware). The alert
  // has to say so: the generic "something went wrong" read as a fault.
  it("Explains a list refused by the public demo", async () => {
    mockedList.mockRejectedValue(
      new ProblemError("Not available in the demo", {
        status: 403,
        title: "Not available in the demo",
        detail: "hidden",
        problem: {
          type: "urn:trustedoss:problem:demo-read-only",
          title: "Not available in the demo",
          status: 403,
          detail: "hidden",
          demo_read_only: true,
          demo_admin_hidden: true,
        } as ProblemDetails,
      }),
    );
    renderPage();
    const alert = await screen.findByTestId("admin-backup-error");
    expect(alert).toHaveTextContent(
      "This screen is not available in the public live demo.",
    );
  });
});
