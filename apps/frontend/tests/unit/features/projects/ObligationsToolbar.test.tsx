/**
 * The obligations toolbar's NOTICE error line used to render the error's
 * `.message`, which on a ProblemError is the backend's English detail. It now
 * goes through problemMessage like every other error line.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ObligationsToolbar } from "@/features/projects/components/ObligationsToolbar";
import { ProblemError } from "@/lib/problem";

function renderToolbar(noticeError: Error | null) {
  return render(
    <ObligationsToolbar
      search=""
      onSearchChange={vi.fn()}
      kinds={[]}
      onKindsChange={vi.fn()}
      categories={[]}
      onCategoriesChange={vi.fn()}
      sort="category"
      onSortChange={vi.fn()}
      order="asc"
      onOrderChange={vi.fn()}
      onDownloadNotice={vi.fn()}
      isNoticeDownloading={false}
      noticeError={noticeError}
    />,
  );
}

describe("ObligationsToolbar NOTICE error", () => {
  it("shows nothing when there is no error", () => {
    renderToolbar(null);
    expect(
      screen.queryByTestId("obligations-download-notice-error"),
    ).not.toBeInTheDocument();
  });

  it("shows the translated failure, not the server's English", () => {
    renderToolbar(
      new ProblemError("database unavailable", {
        status: 500,
        title: "Internal Server Error",
        detail: "database unavailable",
        problem: null,
      }),
    );

    const err = screen.getByTestId("obligations-download-notice-error");
    expect(err).toHaveTextContent("The NOTICE file could not be downloaded.");
    expect(err).toHaveTextContent("The server could not complete this.");
    expect(err).not.toHaveTextContent("database unavailable");
  });
});
