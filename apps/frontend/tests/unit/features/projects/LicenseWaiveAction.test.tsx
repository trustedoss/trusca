/**
 * LicenseWaiveAction: unit tests.
 *
 * The one waive control a forbidden Compliance row carries. Covers:
 *   1. group_admin sees an enabled "Waive..." trigger; opening it shows the
 *      dialog with the target list, reason (required) and expiry.
 *   2. Submit is blocked until a target and a non-empty reason (plus an
 *      expiry for a forbidden waiver) are set, then POSTs the exception
 *      scoped to the chosen component's purl with an ISO-widened expiry.
 *   3. With several targets the POST carries the one the user picked, not
 *      the first.
 *   4. An already-waived target shows the "Waived" badge (text label, not
 *      colour alone) with its reason, an "Un-waive" action that DELETEs the
 *      exception, and the row shows a "{n} waived" count.
 *   5. The trigger is disabled (role-gated, not hidden) for a developer, a
 *      viewer, a read-only snapshot and an unresolved team.
 *
 * The real `useLicenseWaive` hooks run against a mocked `licensePoliciesApi`
 * so the mutation wiring (invalidation, cache seeding) is exercised end to end.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  LicenseWaiveAction,
  type LicenseWaiveTarget,
} from "@/features/projects/components/LicenseWaiveAction";
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
const PURL_B = "pkg:pypi/chardet@5.2.0";

function target(
  purl: string,
  label: string,
  existing: LicenseException | null = null,
): LicenseWaiveTarget {
  return { key: `cv-${label}`, label, purl, existing };
}

const ONE_TARGET = [target(PURL, "pyphen@0.14.0")];

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
        projectRole="group_admin"
        spdxId="GPL-2.0-only"
        targets={ONE_TARGET}
        {...props}
      />
    </QueryClientProvider>,
  );
}

function stateFor(purl: string): HTMLElement {
  const el = screen
    .getAllByTestId("license-waive-state")
    .find((s) => s.getAttribute("data-component-purl") === purl);
  if (!el) throw new Error(`no waive state for ${purl}`);
  return el;
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
    expect(trigger).toHaveTextContent("Waive…");
    // The accessible name carries the license, since the row has one button
    // and a screen reader in table mode hears it out of the row's context.
    expect(trigger).toHaveAccessibleName(/GPL-2\.0-only/);
    await user.click(trigger);

    expect(screen.getByTestId("license-waive-dialog")).toBeInTheDocument();
    // The only open target is preselected: there is nothing to choose.
    expect(screen.getByTestId("license-waive-target")).toBeChecked();
    expect(screen.getByTestId("license-waive-submit")).toBeDisabled();

    await user.type(
      screen.getByTestId("license-waive-reason"),
      "Disjunctive license, MPL chosen",
    );
    expect(screen.getByTestId("license-waive-submit")).toBeEnabled();
  });

  it("blocks submit until an expiry is set when requireExpiry (forbidden waiver)", async () => {
    const user = userEvent.setup();
    mockedAdd.mockResolvedValue(policy([]));
    renderAction({ requireExpiry: true });

    await user.click(screen.getByTestId("license-waive-open"));
    // A reason alone is not enough: the expiry is mandatory for a forbidden
    // waiver (mirrors the server's LICENSE_WAIVE_MAX_DAYS rule).
    await user.type(
      screen.getByTestId("license-waive-reason"),
      "Disjunctive license, MPL chosen",
    );
    expect(screen.getByTestId("license-waive-submit")).toBeDisabled();

    await user.type(screen.getByTestId("license-waive-expires"), "2026-12-31");
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

  it("waives the component the user picked, not the first one", async () => {
    const user = userEvent.setup();
    mockedAdd.mockResolvedValue(policy([]));
    renderAction({
      targets: [target(PURL, "pyphen@0.14.0"), target(PURL_B, "chardet@5.2.0")],
    });

    // One trigger for the row, however many components it lists.
    expect(screen.getAllByTestId("license-waive-open")).toHaveLength(1);
    await user.click(screen.getByTestId("license-waive-open"));

    await user.click(
      within(stateFor(PURL_B)).getByTestId("license-waive-target"),
    );
    expect(
      within(stateFor(PURL)).getByTestId("license-waive-target"),
    ).not.toBeChecked();
    await user.type(screen.getByTestId("license-waive-reason"), "reviewed");
    await user.click(screen.getByTestId("license-waive-submit"));

    await waitFor(() => expect(mockedAdd).toHaveBeenCalledTimes(1));
    expect(mockedAdd.mock.calls[0][1].component_purl).toBe(PURL_B);
  });

  it("shows a waived target with its badge, reason and Un-waive", async () => {
    const user = userEvent.setup();
    mockedDelete.mockResolvedValue(policy([]));
    const existing: LicenseException = {
      spdx_id: "GPL-2.0-only",
      reason: "Dual-licensed, MPL applies",
      component_purl: PURL,
      expires_at: null,
    };
    renderAction({
      targets: [
        target(PURL, "pyphen@0.14.0", existing),
        target(PURL_B, "chardet@5.2.0"),
      ],
    });

    // The row tells the reader something is already overridden, with the
    // reason on hover, before the dialog is opened.
    const count = screen.getByTestId("license-waived-count");
    expect(count).toHaveTextContent("1 waived");
    expect(count).toHaveAttribute(
      "title",
      "pyphen@0.14.0: Dual-licensed, MPL applies",
    );

    await user.click(screen.getByTestId("license-waive-open"));
    const waived = stateFor(PURL);
    expect(waived).toHaveAttribute("data-waived", "true");
    const badge = within(waived).getByTestId("license-waived-badge");
    expect(badge).toHaveTextContent("Waived");
    expect(badge).toHaveAttribute(
      "title",
      expect.stringContaining("Dual-licensed, MPL applies"),
    );
    // A waived component is not a choice; the open one is, and is preselected.
    expect(
      within(waived).queryByTestId("license-waive-target"),
    ).not.toBeInTheDocument();
    expect(
      within(stateFor(PURL_B)).getByTestId("license-waive-target"),
    ).toBeChecked();

    await user.click(within(waived).getByTestId("license-unwaive"));
    await waitFor(() => expect(mockedDelete).toHaveBeenCalledTimes(1));
    expect(mockedDelete).toHaveBeenCalledWith(TEAM_ID, {
      spdx_id: "GPL-2.0-only",
      component_purl: PURL,
    });
  });

  it("keeps submit disabled when every target is already waived", async () => {
    const user = userEvent.setup();
    renderAction({
      targets: [
        target(PURL, "pyphen@0.14.0", {
          spdx_id: "GPL-2.0-only",
          reason: "r",
          component_purl: PURL,
          expires_at: null,
        }),
      ],
    });

    // Still openable: un-waiving happens in the dialog.
    await user.click(screen.getByTestId("license-waive-open"));
    expect(screen.getByTestId("license-waive-all-waived")).toBeInTheDocument();
    expect(screen.getByTestId("license-waive-reason")).toBeDisabled();
    expect(screen.getByTestId("license-waive-submit")).toBeDisabled();
  });

  it("disables the trigger for a developer (role-gated, not hidden)", () => {
    renderAction({ projectRole: "developer" });
    const trigger = screen.getByTestId("license-waive-open");
    expect(trigger).toBeDisabled();
    expect(trigger).toHaveAttribute("data-role-gated", "true");
    expect(trigger).toHaveAttribute(
      "title",
      "Only team admins can waive a license.",
    );
  });

  it("disables the trigger for a viewer, the grade below developer", () => {
    // The check reads "is this grade group_admin or above", so a grade added
    // below developer is refused without touching it. Pinned because the
    // opposite phrasing ("is this developer") is what broke elsewhere: it
    // treats every other grade as more privileged.
    renderAction({ projectRole: "viewer" });
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

  it("disables the trigger when the team is not yet resolved", () => {
    renderAction({ teamId: null });
    const trigger = screen.getByTestId("license-waive-open");
    expect(trigger).toBeDisabled();
    expect(trigger).toHaveAttribute("title", "Project team is still loading.");
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
      targets: [
        target(PURL, "pyphen@0.14.0", {
          spdx_id: "GPL-2.0-only",
          reason: "r",
          component_purl: PURL,
          expires_at: null,
        }),
      ],
    });
    await user.click(screen.getByTestId("license-waive-open"));
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
      expect(screen.getByTestId("license-waive-error")).toHaveTextContent(
        "You do not have permission to waive licenses for this team.",
      ),
    );
  });
});
