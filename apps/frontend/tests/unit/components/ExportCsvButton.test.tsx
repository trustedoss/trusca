/**
 * ExportCsvButton: what every table's export button owes the reader (B5).
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ExportCsvButton } from "@/components/ExportCsvButton";
import { ToastProvider } from "@/components/ui/toast";
import { ProblemError } from "@/lib/problem";

function renderButton(
  onExport: () => Promise<void>,
  extra: { disabled?: boolean; disabledReason?: string } = {},
) {
  return render(
    <ToastProvider>
      <ExportCsvButton
        onExport={onExport}
        namespace="project_detail"
        tooLargeExtension="vulnerabilities_export_too_large"
        tooLargeMessageKey="export.too_large.vulnerabilities"
        data-testid="export-csv"
        {...extra}
      />
    </ToastProvider>,
  );
}

describe("ExportCsvButton", () => {
  it("says the download started", async () => {
    renderButton(() => Promise.resolve());

    await userEvent.click(screen.getByTestId("export-csv"));

    await waitFor(() => {
      expect(
        document.querySelector('[data-toast-key="csv_started"]'),
      ).toBeInTheDocument();
    });
  });

  it("refuses a second click while the first is still running", async () => {
    // Two clicks would start two downloads of the same rows, and on a large
    // export the second is a second full scan of the table.
    let release: () => void = () => {};
    const onExport = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          release = resolve;
        }),
    );
    renderButton(onExport);

    const button = screen.getByTestId("export-csv");
    await userEvent.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    expect(button).toHaveAttribute("data-exporting", "true");

    await userEvent.click(button);
    expect(onExport).toHaveBeenCalledTimes(1);

    release();
    await waitFor(() => expect(button).not.toBeDisabled());
  });

  it("names the table when the export is refused for being too large", async () => {
    // "The export is too large" without saying which export is not much of
    // an answer to someone with three tabs open.
    renderButton(() =>
      Promise.reject(
        new ProblemError("too large", {
          status: 413,
          title: "Vulnerability Export Too Large",
          detail: "narrow the filters and retry",
          problem: {
            type: "https://docs.trustedoss.io/errors/vulnerabilities-export-too-large",
            title: "Vulnerability Export Too Large",
            status: 413,
            detail: "narrow the filters and retry",
            vulnerabilities_export_too_large: true,
          },
        }),
      ),
    );

    await userEvent.click(screen.getByTestId("export-csv"));

    await waitFor(() => {
      const toast = document.querySelector(
        '[data-toast-key="vulnerabilities_export_too_large"]',
      );
      expect(toast).toBeInTheDocument();
      expect(toast).toHaveAttribute("data-tone", "error");
      expect(toast?.textContent).toMatch(/findings/i);
    });
  });

  it("still says something useful for a failure that is not the cap", async () => {
    renderButton(() =>
      Promise.reject(
        new ProblemError("boom", {
          status: 500,
          title: "Server Error",
          detail: "",
          problem: null,
        }),
      ),
    );

    await userEvent.click(screen.getByTestId("export-csv"));

    await waitFor(() => {
      const toast = document.querySelector('[data-toast-key="export_failed"]');
      expect(toast).toBeInTheDocument();
      expect(toast).toHaveAttribute("data-tone", "error");
      // Not an empty toast, and not the raw English detail from the server.
      expect((toast?.textContent ?? "").length).toBeGreaterThan(10);
    });
  });

  it("refuses to export when the caller blocked it, and says why", async () => {
    // The vulnerabilities tab blocks this while its client-side VEX filter is
    // on, because that filter narrows the screen and not the file. Nothing
    // asserted on the blocked state before, so deleting the guard was silent.
    const onExport = vi.fn(() => Promise.resolve());
    renderButton(onExport, {
      disabled: true,
      disabledReason: "Clear the filter first.",
    });

    const button = screen.getByTestId("export-csv");
    await userEvent.click(button);
    expect(onExport).not.toHaveBeenCalled();

    // Still reachable by keyboard, and the reason is bound to it. A plain
    // `disabled` button leaves the tab order, so the reader never hears it.
    expect(button).toHaveAttribute("aria-disabled", "true");
    expect(button).not.toBeDisabled();
    const describedBy = button.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy ?? "")?.textContent).toBe(
      "Clear the filter first.",
    );
  });

  it("comes back to life after a failure", async () => {
    // A refused export is a thing the reader fixes by narrowing the filter
    // and clicking again; a button stuck disabled would not let them.
    renderButton(() => Promise.reject(new Error("nope")));

    const button = screen.getByTestId("export-csv");
    await userEvent.click(button);

    await waitFor(() => expect(button).not.toBeDisabled());
  });
});
