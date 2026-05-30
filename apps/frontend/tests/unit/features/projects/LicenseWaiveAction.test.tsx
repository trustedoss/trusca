/**
 * LicenseWaiveAction — unit tests.
 *
 * Per-component license waive control on the Compliance tab. Covers:
 *   1. team_admin sees an enabled "Waive" trigger; opening it shows the dialog
 *      with reason (required) + optional expiry.
 *   2. Submit is blocked until a non-empty reason is entered, then POSTs the
 *      exception with the scoped purl + ISO-widened expiry.
 *   3. An already-waived (spdx, purl) pair renders the "Waived" badge (text
 *      label, not colour alone) + reason tooltip + an "Un-waive" action that
 *      DELETEs the exception.
 *   4. A developer sees the trigger disabled (role-gated, not hidden).
 *   5. A read-only historical snapshot disables the trigger.
 *
 * The real `useLicenseWaive` hooks run against a mocked `licensePoliciesApi`
 * so the mutation wiring (invalidation, cache seeding) is exercised end to end.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LicenseWaiveAction } from "@/features/projects/components/LicenseWaiveAction";
import type { LicenseException } from "@/features/projects/api/useLicenseWaive";
import type { LicensePolicyOut } from "@/lib/licensePoliciesApi";
import { ProblemError } from "@/lib/problem";

vi.mock("@/lib/licensePoliciesApi", async () => {
  return {
    addTeamLicenseException: vi.fn(),
    deleteTeamLicenseException: vi.fn(),
    getTeamPolicy: vi.fn(),
  };
});

import {
  addTeamLicenseException,
  deleteTeamLicenseException,
} from "@/lib/licensePoliciesApi";

const mockedAdd = vi.mocked(addTeamLicenseException);
const mockedDelete = vi.mocked(deleteTeamLicenseException);

const TEAM_ID = "00000000-0000-0000-0000-team00000001";
const PURL = "pkg:pypi/pyphen@0.14.0";

function policy(exceptions: LicenseException[]): LicensePolicyOut {
  return {
    id: "00000000-0000-0000-0000-policy0000001",
    organization_id: "00000000-0000-0000-0000-org000000001",
    team_id: TEAM_ID,
    name: null,
    category_overrides: {},
    license_exceptions: exceptions,
    unknown_license_category: "conditional",
    compound_operator_strategy: {
      AND: "most_restrictive",
      OR: "least_restrictive",
      WITH: "most_restrictive",
    },
    enabled: true,
    created_by_user_id: null,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
  };
}

function renderAction(
  props: Partial<React.ComponentProps<typeof LicenseWaiveAction>> = {},
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <LicenseWaiveAction
        projectId="p1"
        teamId={TEAM_ID}
        projectRole="team_admin"
        spdxId="GPL-2.0-only"
        componentLabel="pyphen@0.14.0"
        componentPurl={PURL}
        existing={null}
        {...props}
      />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("LicenseWaiveAction", () => {
  it("opens the dialog and requires a reason before submitting", async () => {
    const user = userEvent.setup();
    mockedAdd.mockResolvedValue(policy([]));
    renderAction();

    const trigger = screen.getByTestId("license-waive-open");
    expect(trigger).toBeEnabled();
    await user.click(trigger);

    expect(screen.getByTestId("license-waive-dialog")).toBeInTheDocument();
    // Submit is disabled with an empty reason.
    expect(screen.getByTestId("license-waive-submit")).toBeDisabled();

    await user.type(
      screen.getByTestId("license-waive-reason"),
      "Disjunctive license, MPL chosen",
    );
    expect(screen.getByTestId("license-waive-submit")).toBeEnabled();
  });

  it("POSTs the exception scoped to the component purl with widened expiry", async () => {
    const user = userEvent.setup();
    mockedAdd.mockResolvedValue(policy([]));
    renderAction();

    await user.click(screen.getByTestId("license-waive-open"));
    await user.type(
      screen.getByTestId("license-waive-reason"),
      "  legal cleared  ",
    );
    await user.type(screen.getByTestId("license-waive-expires"), "2026-12-31");
    await user.click(screen.getByTestId("license-waive-submit"));

    await waitFor(() => expect(mockedAdd).toHaveBeenCalledTimes(1));
    expect(mockedAdd).toHaveBeenCalledWith(TEAM_ID, {
      spdx_id: "GPL-2.0-only",
      reason: "legal cleared",
      component_purl: PURL,
      expires_at: "2026-12-31T00:00:00Z",
    });
  });

  it("renders a Waived badge + reason tooltip + Un-waive when already waived", async () => {
    const user = userEvent.setup();
    mockedDelete.mockResolvedValue(policy([]));
    const existing: LicenseException = {
      spdx_id: "GPL-2.0-only",
      reason: "Dual-licensed, MPL applies",
      component_purl: PURL,
      expires_at: null,
    };
    renderAction({ existing });

    const badge = screen.getByTestId("license-waived-badge");
    expect(badge).toHaveTextContent("Waived");
    expect(badge).toHaveAttribute(
      "title",
      expect.stringContaining("Dual-licensed, MPL applies"),
    );

    await user.click(screen.getByTestId("license-unwaive"));
    await waitFor(() => expect(mockedDelete).toHaveBeenCalledTimes(1));
    expect(mockedDelete).toHaveBeenCalledWith(TEAM_ID, {
      spdx_id: "GPL-2.0-only",
      component_purl: PURL,
    });
  });

  it("disables the trigger for a developer (role-gated, not hidden)", () => {
    renderAction({ projectRole: "developer" });
    const trigger = screen.getByTestId("license-waive-open");
    expect(trigger).toBeDisabled();
    expect(trigger).toHaveAttribute("data-role-gated", "true");
  });

  it("disables the trigger on a read-only historical snapshot", () => {
    renderAction({ readOnly: true });
    const trigger = screen.getByTestId("license-waive-open");
    expect(trigger).toBeDisabled();
    expect(trigger).toHaveAttribute("data-readonly-gated", "true");
  });

  it("disables the trigger when the component has no purl", () => {
    renderAction({ componentPurl: null });
    const trigger = screen.getByTestId("license-waive-open");
    expect(trigger).toBeDisabled();
    expect(trigger).toHaveAttribute("title", expect.stringContaining(""));
  });

  it("disables the trigger when the team is not yet resolved", () => {
    renderAction({ teamId: null });
    expect(screen.getByTestId("license-waive-open")).toBeDisabled();
  });

  it("surfaces an un-waive error inline", async () => {
    const user = userEvent.setup();
    mockedDelete.mockRejectedValue(
      new ProblemError("boom", {
        status: 500,
        title: "Server Error",
        detail: "kaboom",
        problem: null,
      }),
    );
    renderAction({
      existing: {
        spdx_id: "GPL-2.0-only",
        reason: "r",
        component_purl: PURL,
        expires_at: null,
      },
    });
    await user.click(screen.getByTestId("license-unwaive"));
    await waitFor(() =>
      expect(screen.getByTestId("license-unwaive-error")).toBeInTheDocument(),
    );
  });

  it("maps a 422 to the malformed-waiver message", async () => {
    const user = userEvent.setup();
    mockedAdd.mockRejectedValue(
      new ProblemError("bad", {
        status: 422,
        title: "Unprocessable",
        detail: "bad reason",
        problem: null,
      }),
    );
    renderAction();
    await user.click(screen.getByTestId("license-waive-open"));
    await user.type(screen.getByTestId("license-waive-reason"), "x");
    await user.click(screen.getByTestId("license-waive-submit"));
    await waitFor(() =>
      expect(screen.getByTestId("license-waive-error")).toHaveTextContent(
        /reason|rejected|거부|사유/,
      ),
    );
  });

  it("surfaces a 403 as a permission error in the dialog", async () => {
    const user = userEvent.setup();
    mockedAdd.mockRejectedValue(
      new ProblemError("forbidden", {
        status: 403,
        title: "Forbidden",
        detail: "nope",
        problem: null,
      }),
    );
    renderAction();

    await user.click(screen.getByTestId("license-waive-open"));
    await user.type(screen.getByTestId("license-waive-reason"), "reason");
    await user.click(screen.getByTestId("license-waive-submit"));

    await waitFor(() =>
      expect(screen.getByTestId("license-waive-error")).toBeInTheDocument(),
    );
  });
});
