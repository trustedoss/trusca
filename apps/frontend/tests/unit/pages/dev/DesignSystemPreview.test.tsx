/**
 * Smoke test for the W11-A dev-only design preview page.
 *
 * This page is the visual confirm gate for the new token set; we don't
 * snapshot it (snapshots are banned per CLAUDE.md) but we DO verify:
 *   1. It renders without throwing.
 *   2. The five risk severity labels are present (color-not-alone signal).
 *   3. The four radius hierarchy steps are present.
 *   4. Buttons and a sample dense-row table render.
 *
 * These assertions ensure a future token rename / Tailwind config slip
 * fails this test instead of silently breaking the preview surface.
 */
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DesignSystemPreview } from "@/pages/dev/DesignSystemPreview";

describe("DesignSystemPreview (W11-A)", () => {
  it("renders the page header", () => {
    render(<DesignSystemPreview />);
    expect(
      screen.getByRole("heading", { name: /Vercel base \+ Linear polish/i }),
    ).toBeInTheDocument();
  });

  it("renders all five risk severity labels with text (not color alone)", () => {
    render(<DesignSystemPreview />);
    // Each severity word must appear at least once in the swatch grid and
    // again as a row-status badge. Use getAllByText so multiple matches are OK.
    for (const label of ["Critical", "High", "Medium", "Low", "Info"]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
  });

  it("renders the four radius hierarchy samples", () => {
    render(<DesignSystemPreview />);
    expect(screen.getByText(/rounded-sm · 4px/)).toBeInTheDocument();
    expect(screen.getByText(/rounded-md · 6px/)).toBeInTheDocument();
    expect(screen.getByText(/rounded-lg · 8px/)).toBeInTheDocument();
    expect(screen.getByText(/rounded-xl · 12px/)).toBeInTheDocument();
  });

  it("renders the three shadow elevation samples", () => {
    render(<DesignSystemPreview />);
    expect(screen.getByText("shadow-sm")).toBeInTheDocument();
    expect(screen.getByText("shadow-md")).toBeInTheDocument();
    expect(screen.getByText("shadow-lg")).toBeInTheDocument();
  });

  it("renders the button variants section", () => {
    render(<DesignSystemPreview />);
    expect(screen.getByRole("button", { name: "Deploy" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "View logs" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });

  it("renders the sample dense-row table", () => {
    render(<DesignSystemPreview />);
    // `frontend-admin` / `backend-api` appear both in the Card samples and
    // in the table — getAllByText so the duplication is intentional.
    expect(screen.getAllByText("frontend-admin").length).toBeGreaterThan(0);
    expect(screen.getAllByText("backend-api").length).toBeGreaterThan(0);
    expect(screen.getByText("mobile-app")).toBeInTheDocument();
  });

  it("renders the W12-E living-reference showcases", () => {
    render(<DesignSystemPreview />);
    // Typography primitives, the EmptyState medallion, and a toast trigger.
    expect(
      screen.getByText(/PageTitle — 18px semibold/),
    ).toBeInTheDocument();
    expect(screen.getByText("No projects yet")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Trigger success toast" }),
    ).toBeInTheDocument();
  });
});
