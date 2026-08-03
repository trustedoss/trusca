/**
 * RemediationTab — unit tests (v2.2 b3 frontend).
 *
 * Coverage targets:
 *   - Preview renders the proposed bumps + the lockfile warning + the
 *     manifest_source indicator.
 *   - Preview no-manifest / no-changes empty states render.
 *   - Create-PR mutation calls the API and shows the created PR as a SAFE
 *     external link (target=_blank, rel=noopener).
 *   - Not-opted-in (409) path shows inline guidance — no crash.
 *   - Non-team_admin is read-only (no create button; guidance shown).
 *   - PR list renders with status badges.
 *   - Preview API error surfaces the RFC 7807 detail.
 *
 * The API wire layer (`@/lib/remediationApi`) and the overview hook (for the
 * project-scoped role) are mocked per the existing SettingsTab/VexImport
 * patterns.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RemediationTab } from "@/features/projects/components/RemediationTab";
import { ProblemError } from "@/lib/problem";
import type {
  NpmDryRunResponse,
  RemediationPullRequest,
  RemediationPullRequestListPage,
} from "@/lib/remediationApi";

// --- Mock the wire layer. ---------------------------------------------------
vi.mock("@/lib/remediationApi", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/remediationApi")>(
      "@/lib/remediationApi",
    );
  return {
    ...actual,
    npmDryRun: vi.fn(),
    createNpmPullRequest: vi.fn(),
    listRemediationPullRequests: vi.fn(),
  };
});

// --- Mock the overview hook so we can drive the project-scoped role. --------
const overviewData: { current_user_role: string } = {
  current_user_role: "team_admin",
};
vi.mock("@/features/projects/api/useProjectOverview", () => ({
  useProjectOverview: () => ({ data: overviewData }),
  projectOverviewKey: (id: string) => ["projects", id, "overview"],
}));

import {
  createNpmPullRequest,
  listRemediationPullRequests,
  npmDryRun,
} from "@/lib/remediationApi";

const mockedDryRun = vi.mocked(npmDryRun);
const mockedCreatePr = vi.mocked(createNpmPullRequest);
const mockedListPrs = vi.mocked(listRemediationPullRequests);

function setRole(role: "developer" | "team_admin" | "super_admin") {
  overviewData.current_user_role = role;
}

function dryRunResponse(
  overrides: Partial<NpmDryRunResponse> = {},
): NpmDryRunResponse {
  return {
    project_id: "proj-1",
    scan_id: "scan-1",
    ecosystem: "npm",
    manifest_source: "preserved_source",
    manifest_found: true,
    changed: true,
    edited_manifest: '{"name":"demo"}',
    recommendations: [
      {
        package: "lodash",
        current_version: "4.17.20",
        recommended_version: "4.17.21",
      },
    ],
    changes: [
      {
        package: "lodash",
        section: "dependencies",
        before: "^4.17.20",
        after: "^4.17.21",
        changed: true,
      },
    ],
    warnings: [
      {
        code: "lockfile_regeneration_required",
        package: null,
        detail: "run `npm install` to regenerate package-lock.json",
      },
    ],
    notes: [],
    ...overrides,
  };
}

function prRecord(
  overrides: Partial<RemediationPullRequest> = {},
): RemediationPullRequest {
  return {
    id: "pr-1",
    project_id: "proj-1",
    ecosystem: "npm",
    repository_full_name: "acme/widget",
    head_branch: "trustedoss/remediation-abc",
    base_branch: "main",
    pr_number: 42,
    pr_url: "https://github.com/acme/widget/pull/42",
    status: "open",
    package_changes: [{ package: "lodash", from: "4.17.20", to: "4.17.21" }],
    created_at: "2026-05-25T12:00:00Z",
    updated_at: "2026-05-25T12:00:01Z",
    ...overrides,
  };
}

function listPage(
  items: RemediationPullRequest[] = [],
): RemediationPullRequestListPage {
  return { items, total: items.length };
}

function renderTab() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <RemediationTab projectId="proj-1" />
    </QueryClientProvider>,
  );
}

describe("RemediationTab", () => {
  beforeEach(() => {
    mockedDryRun.mockReset();
    mockedCreatePr.mockReset();
    mockedListPrs.mockReset();
    setRole("team_admin");
    mockedListPrs.mockResolvedValue(listPage());
  });

  it("preview renders the proposed bumps, the lockfile warning, and the manifest source", async () => {
    mockedDryRun.mockResolvedValue(dryRunResponse());
    renderTab();

    await userEvent.click(screen.getByTestId("remediation-preview-button"));

    await waitFor(() => {
      expect(screen.getByTestId("remediation-bumps-table")).toBeInTheDocument();
    });

    const row = screen.getByTestId("remediation-bump-row");
    expect(within(row).getByText("lodash")).toBeInTheDocument();
    expect(within(row).getByText("4.17.20")).toBeInTheDocument();
    expect(within(row).getByText("4.17.21")).toBeInTheDocument();

    // The lockfile-regeneration warning surfaces.
    const warning = screen.getByTestId("remediation-warning");
    expect(warning).toHaveAttribute("data-code", "lockfile_regeneration_required");
    expect(warning).toHaveTextContent(/npm install/);

    // The manifest-source indicator surfaces.
    const src = screen.getByTestId("remediation-manifest-source");
    expect(src).toHaveAttribute("data-source", "preserved_source");

    expect(mockedDryRun).toHaveBeenCalledWith("proj-1", {});
  });

  it("preview shows the no-manifest empty state", async () => {
    mockedDryRun.mockResolvedValue(
      dryRunResponse({
        manifest_source: "none",
        manifest_found: false,
        changed: false,
        recommendations: [],
        changes: [],
        warnings: [],
      }),
    );
    renderTab();
    await userEvent.click(screen.getByTestId("remediation-preview-button"));
    await waitFor(() => {
      expect(screen.getByTestId("remediation-no-manifest")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("remediation-bumps-table"),
    ).not.toBeInTheDocument();
  });

  it("preview shows the no-changes empty state when nothing to bump", async () => {
    mockedDryRun.mockResolvedValue(
      dryRunResponse({ changed: false, recommendations: [], changes: [] }),
    );
    renderTab();
    await userEvent.click(screen.getByTestId("remediation-preview-button"));
    await waitFor(() => {
      expect(screen.getByTestId("remediation-no-changes")).toBeInTheDocument();
    });
  });

  it("preview surfaces the RFC 7807 detail on an API error", async () => {
    mockedDryRun.mockRejectedValue(
      new ProblemError("boom", {
        status: 422,
        title: "Unprocessable",
        detail: "package.json could not be edited",
        problem: null,
      }),
    );
    renderTab();
    await userEvent.click(screen.getByTestId("remediation-preview-button"));
    await waitFor(() => {
      expect(screen.getByTestId("remediation-preview-error")).toHaveTextContent(
        "package.json could not be edited",
      );
    });
  });

  it("create-PR calls the API and shows the created PR as a safe external link", async () => {
    mockedCreatePr.mockResolvedValue(prRecord());
    renderTab();

    await userEvent.click(screen.getByTestId("remediation-create-pr-button"));

    await waitFor(() => {
      expect(
        screen.getByTestId("remediation-create-success"),
      ).toBeInTheDocument();
    });

    const link = screen.getByTestId("remediation-created-pr-link");
    expect(link).toHaveAttribute("href", "https://github.com/acme/widget/pull/42");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(mockedCreatePr).toHaveBeenCalledWith("proj-1", {});
  });

  it("create-PR no-op (204 → null) shows the up-to-date guidance", async () => {
    mockedCreatePr.mockResolvedValue(null);
    renderTab();
    await userEvent.click(screen.getByTestId("remediation-create-pr-button"));
    await waitFor(() => {
      expect(screen.getByTestId("remediation-create-noop")).toBeInTheDocument();
    });
  });

  it("not-opted-in (409) shows inline guidance and does not crash", async () => {
    mockedCreatePr.mockRejectedValue(
      new ProblemError("not opted in", {
        status: 409,
        title: "Conflict",
        detail: "Project is not opted in",
        problem: null,
      }),
    );
    renderTab();
    await userEvent.click(screen.getByTestId("remediation-create-pr-button"));
    await waitFor(() => {
      expect(
        screen.getByTestId("remediation-pr-not-opted-in"),
      ).toBeInTheDocument();
    });
    // No raw error alert / crash.
    expect(
      screen.queryByTestId("remediation-create-error"),
    ).not.toBeInTheDocument();
  });

  it("is read-only for a non-team_admin (no create button, guidance shown)", () => {
    setRole("developer");
    renderTab();
    expect(
      screen.queryByTestId("remediation-create-pr-button"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("remediation-pr-role-gated")).toBeInTheDocument();
  });

  it("renders the PR list with status badges and links", async () => {
    mockedListPrs.mockResolvedValue(
      listPage([
        prRecord({ id: "pr-1", pr_number: 42, status: "open" }),
        prRecord({
          id: "pr-2",
          pr_number: 7,
          pr_url: "https://github.com/acme/widget/pull/7",
          status: "failed",
        }),
      ]),
    );
    renderTab();

    await waitFor(() => {
      expect(screen.getAllByTestId("remediation-pr-row")).toHaveLength(2);
    });

    const badges = screen.getAllByTestId("remediation-pr-status");
    const statuses = badges.map((b) => b.getAttribute("data-status"));
    expect(statuses).toEqual(["open", "failed"]);

    const links = screen.getAllByTestId("remediation-pr-link");
    expect(links[0]).toHaveAttribute(
      "href",
      "https://github.com/acme/widget/pull/42",
    );
    expect(links[0]).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("shows the empty PR list state when there are none", async () => {
    mockedListPrs.mockResolvedValue(listPage());
    renderTab();
    await waitFor(() => {
      expect(
        screen.getByTestId("remediation-pr-list-empty"),
      ).toBeInTheDocument();
    });
  });
});
