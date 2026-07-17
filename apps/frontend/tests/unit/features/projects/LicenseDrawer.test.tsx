/**
 * LicenseDrawer — unit tests (PR #12).
 *
 * Read-only drawer (no transitions, no audit log). Covers:
 *   - meta panel (SPDX id, category, kind, OSI / FSF / deprecated flags).
 *   - reference URL: http(s) → clickable `<a>`; non-http(s) → plain text
 *     (XSS scheme guard).
 *   - ort_match: null → empty message; populated → collapsible toggle that
 *     reveals the field rows.
 *   - affected_components: cross-link button rewrites `?tab=components&drawer=…`
 *     and clears `?license=<id>`.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { LicenseDetailResponse } from "@/features/projects/api/licensesApi";
import { LicenseDrawer } from "@/features/projects/components/LicenseDrawer";
import { ProblemError } from "@/lib/problem";

vi.mock("@/features/projects/api/licensesApi", async () => {
  return {
    listProjectLicenses: vi.fn(),
    getLicenseFinding: vi.fn(),
  };
});

import { getLicenseFinding } from "@/features/projects/api/licensesApi";

const mockedGet = vi.mocked(getLicenseFinding);

function detail(
  overrides: Partial<LicenseDetailResponse> = {},
): LicenseDetailResponse {
  return {
    id: "00000000-0000-0000-0000-licfind00001",
    license_id: "00000000-0000-0000-0000-license00001",
    spdx_id: "MIT",
    name: "MIT License",
    summary: null,
    summary_ko: null,
    category: "allowed",
    is_osi_approved: true,
    is_fsf_libre: true,
    is_deprecated_license_id: false,
    reference_url: "https://opensource.org/licenses/MIT",
    finding_kind: "concluded",
    ort_match: null,
    affected_components: [],
    affected_components_truncated: false,
    affected_components_total: 0,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
    ...overrides,
  };
}

/**
 * The drawer reads the URL only via the cross-link pivot. We render under
 * MemoryRouter so `useSearchParams` is available; assertions read the URL
 * via a hidden probe component.
 */
function URLProbe() {
  const [params] = useSearchParams();
  return (
    <div
      data-testid="url-probe"
      data-tab={params.get("tab") ?? ""}
      data-drawer={params.get("drawer") ?? ""}
      data-license={params.get("license") ?? ""}
    />
  );
}

function renderDrawer(
  findingId: string | null,
  open = true,
  onOpenChange: (open: boolean) => void = () => {},
  initialEntries: string[] = ["/projects/p1?license=00000000-0000-0000-0000-licfind00001"],
  reviewFlag: "behavioral_use" | "non_commercial" | null = null,
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>
        <LicenseDrawer
          open={open}
          findingId={findingId}
          reviewFlag={reviewFlag}
          onOpenChange={onOpenChange}
        />
        <URLProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("LicenseDrawer", () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders nothing when closed (no fetch)", () => {
    renderDrawer("any-id", false);
    expect(screen.queryByTestId("license-drawer")).not.toBeInTheDocument();
    expect(mockedGet).not.toHaveBeenCalled();
  });

  it("shows skeleton while the detail is loading", () => {
    mockedGet.mockReturnValue(new Promise(() => {})); // never resolves
    renderDrawer("00000000-0000-0000-0000-licfind00001");
    expect(screen.getByTestId("license-drawer")).toBeInTheDocument();
    expect(screen.getByTestId("license-drawer-loading")).toBeInTheDocument();
  });

  it("renders the meta panel with SPDX id, category badge, and OSI / FSF flags", async () => {
    mockedGet.mockResolvedValueOnce(detail());
    renderDrawer("00000000-0000-0000-0000-licfind00001");
    await waitFor(() => {
      expect(screen.getByTestId("license-drawer-meta")).toBeInTheDocument();
    });
    expect(screen.getByTestId("license-drawer-spdx-id").textContent).toContain(
      "MIT",
    );
    expect(
      screen.getByTestId("license-category-badge-allowed"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("license-drawer-flag-osi")).toBeInTheDocument();
    expect(screen.getByTestId("license-drawer-flag-fsf")).toBeInTheDocument();
    // The reference URL is http(s) → rendered as a clickable anchor with
    // rel="noopener noreferrer" and target="_blank".
    const link = screen.getByTestId("license-drawer-reference");
    expect(link.tagName).toBe("A");
    expect(link).toHaveAttribute(
      "href",
      "https://opensource.org/licenses/MIT",
    );
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("renders deprecated badge when is_deprecated_license_id is true", async () => {
    mockedGet.mockResolvedValueOnce(detail({ is_deprecated_license_id: true }));
    renderDrawer("any-id");
    await waitFor(() => {
      expect(
        screen.getByTestId("license-drawer-flag-deprecated"),
      ).toBeInTheDocument();
    });
  });

  it("shows the AI review-flag section with a description when a flag is passed (Phase D)", async () => {
    mockedGet.mockResolvedValueOnce(detail({ name: "Llama 2 Community License" }));
    renderDrawer(
      "any-id",
      true,
      () => {},
      ["/projects/p1?license=any-id"],
      "behavioral_use",
    );
    const section = await screen.findByTestId("license-drawer-review-flag");
    expect(section).toHaveAttribute("data-review-flag", "behavioral_use");
    // The class-specific description text renders (color is not the only signal).
    expect(section.textContent).toContain("Behavioral-use");
    expect(
      within(section).getByTestId("license-review-flag-badge"),
    ).toBeInTheDocument();
  });

  it("omits the review-flag section when no flag is passed", async () => {
    mockedGet.mockResolvedValueOnce(detail());
    renderDrawer("any-id");
    await waitFor(() => {
      expect(screen.getByTestId("license-drawer-meta")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("license-drawer-review-flag"),
    ).not.toBeInTheDocument();
  });

  it("renders non-http reference_url as plain text (XSS scheme guard)", async () => {
    mockedGet.mockResolvedValueOnce(
      detail({
        reference_url: "javascript:alert(1)",
      }),
    );
    renderDrawer("any-id");
    await waitFor(() => {
      expect(
        screen.getByTestId("license-drawer-reference-unsafe"),
      ).toBeInTheDocument();
    });
    // The unsafe variant is text only — no <a> at the safe testid.
    expect(
      screen.queryByTestId("license-drawer-reference"),
    ).not.toBeInTheDocument();
  });

  it("shows an empty message when ort_match is null", async () => {
    mockedGet.mockResolvedValueOnce(detail({ ort_match: null }));
    renderDrawer("any-id");
    await waitFor(() => {
      expect(
        screen.getByTestId("license-drawer-ort-match"),
      ).toBeInTheDocument();
    });
    // Toggle button + fields grid are absent when ort_match is null.
    expect(
      screen.queryByTestId("license-drawer-ort-toggle"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("license-drawer-ort-fields"),
    ).not.toBeInTheDocument();
  });

  it("toggles ort_match fields when ort_match has known keys", async () => {
    mockedGet.mockResolvedValueOnce(
      detail({
        ort_match: {
          rule_name: "license-rule-x",
          score: 0.97,
          matched_text: "Permission is hereby granted, free of charge",
        },
      }),
    );
    renderDrawer("any-id");
    await waitFor(() => {
      expect(
        screen.getByTestId("license-drawer-ort-toggle"),
      ).toBeInTheDocument();
    });
    // Collapsed by default — fields hidden.
    expect(
      screen.queryByTestId("license-drawer-ort-fields"),
    ).not.toBeInTheDocument();

    await userEvent.click(screen.getByTestId("license-drawer-ort-toggle"));
    const fields = screen.getByTestId("license-drawer-ort-fields");
    expect(fields).toBeInTheDocument();
    // Each field row carries the raw key on a `data-ort-key` attribute so
    // the assertion is locale-agnostic.
    const keys = Array.from(fields.querySelectorAll("[data-ort-key]")).map(
      (el) => el.getAttribute("data-ort-key"),
    );
    expect(keys).toEqual(
      expect.arrayContaining(["rule_name", "score", "matched_text"]),
    );

    // Toggle again → fields disappear.
    await userEvent.click(screen.getByTestId("license-drawer-ort-toggle"));
    expect(
      screen.queryByTestId("license-drawer-ort-fields"),
    ).not.toBeInTheDocument();
  });

  it("lists affected components and pivots to ComponentDrawer on click", async () => {
    const onOpenChange = vi.fn();
    mockedGet.mockResolvedValueOnce(
      detail({
        affected_components: [
          {
            component_version_id: "00000000-0000-0000-0000-cv0000000001",
            component_name: "alpha",
            version: "1.0.0",
            kind: "concluded",
            source_path: "LICENSE",
          },
          {
            component_version_id: "00000000-0000-0000-0000-cv0000000002",
            component_name: "bravo",
            version: "2.0.0",
            kind: "declared",
            source_path: null,
          },
        ],
      }),
    );
    renderDrawer(
      "00000000-0000-0000-0000-licfind00001",
      true,
      onOpenChange,
      ["/projects/p1?license=00000000-0000-0000-0000-licfind00001"],
    );
    await waitFor(() => {
      expect(
        screen.getByTestId("license-drawer-affected"),
      ).toBeInTheDocument();
    });
    const rows = screen.getAllByTestId("license-drawer-affected-row");
    expect(rows).toHaveLength(2);
    // Each row exposes its component_version_id verbatim — locale-agnostic.
    expect(rows[0]).toHaveAttribute(
      "data-component-version-id",
      "00000000-0000-0000-0000-cv0000000001",
    );

    // Click the first cross-link → URL pivots, drawer requests close.
    const link = rows[0].querySelector(
      "[data-testid='license-drawer-affected-link']",
    ) as HTMLElement;
    expect(link).toBeTruthy();
    await userEvent.click(link);

    await waitFor(() => {
      const probe = screen.getByTestId("url-probe");
      expect(probe).toHaveAttribute("data-tab", "components");
      expect(probe).toHaveAttribute(
        "data-drawer",
        "00000000-0000-0000-0000-cv0000000001",
      );
      // license=<id> was cleared during the pivot so the two drawers don't
      // stack.
      expect(probe).toHaveAttribute("data-license", "");
    });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("labels a detected (first-party) finding with its source_path (PR-A3)", async () => {
    mockedGet.mockResolvedValueOnce(
      detail({
        finding_kind: "detected",
        affected_components: [
          {
            component_version_id: "00000000-0000-0000-0000-cv0000000010",
            component_name: "my-app",
            version: "0.0.0",
            kind: "detected",
            source_path: "src/lib/vendored/foo.go",
          },
        ],
      }),
    );
    renderDrawer("00000000-0000-0000-0000-licfind00001");
    await waitFor(() => {
      expect(screen.getByTestId("license-drawer-affected")).toBeInTheDocument();
    });
    // The header kind badge shows the detected provenance. The affected row
    // also renders one, so there are two detected badges in the tree.
    expect(
      screen.getAllByTestId("license-kind-badge-detected").length,
    ).toBeGreaterThanOrEqual(1);
    // The header meta wrapper carries the kind badge.
    expect(
      screen.getByTestId("license-drawer-kind"),
    ).toBeInTheDocument();
    // The affected row carries the labeled source path in mono.
    const sourcePath = screen.getByTestId(
      "license-drawer-affected-source-path",
    );
    expect(sourcePath).toHaveTextContent("src/lib/vendored/foo.go");
  });

  it("renders the RFC 7807 detail in the drawer error alert", async () => {
    mockedGet.mockRejectedValueOnce(
      new ProblemError("not found", {
        status: 404,
        title: "Not Found",
        detail: "License finding 4242 not found.",
        problem: null,
      }),
    );
    renderDrawer("missing-id");
    await waitFor(() => {
      expect(
        screen.getByTestId("license-drawer-error"),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("license-drawer-error").textContent,
    ).toContain("License finding 4242 not found.");
  });
});
