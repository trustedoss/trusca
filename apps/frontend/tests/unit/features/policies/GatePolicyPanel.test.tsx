/**
 * Build-gate panel: the distinction the form has to preserve.
 *
 * An empty threshold and a threshold of zero mean opposite things. Empty means
 * the team has not decided and follows its organization; zero means the team
 * decided that any score at all blocks. A number input cannot tell them apart,
 * so each field carries its own override switch, and these tests pin that the
 * payload reflects the switch rather than the emptiness of the box.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GatePolicyPanel } from "@/features/policies/GatePolicyPanel";

const getTeamGatePolicy = vi.fn();
const upsertTeamGatePolicy = vi.fn();
const deleteTeamGatePolicy = vi.fn();

// Spread the real module rather than listing its exports: a stub that names
// them one by one goes stale the moment the module grows a constant, and it
// fails as an unrelated crash rather than as a missing mock.
vi.mock("@/lib/gatePoliciesApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/gatePoliciesApi")>()),
  getTeamGatePolicy: (...args: unknown[]) => getTeamGatePolicy(...args),
  upsertTeamGatePolicy: (...args: unknown[]) => upsertTeamGatePolicy(...args),
  deleteTeamGatePolicy: (...args: unknown[]) => deleteTeamGatePolicy(...args),
  upsertOrgGatePolicy: vi.fn(),
  getEffectiveGatePolicy: vi.fn(),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

function renderPanel(canEdit = true) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <GatePolicyPanel teamId="team-1" canEdit={canEdit} />
    </QueryClientProvider>,
  );
}

describe("GatePolicyPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    upsertTeamGatePolicy.mockResolvedValue({});
    deleteTeamGatePolicy.mockResolvedValue(undefined);
  });

  it("sends null for a field the team has not taken over", async () => {
    getTeamGatePolicy.mockRejectedValue(
      Object.assign(new Error("not found"), { status: 404, name: "ProblemError" }),
    );
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("gate-policy-save")).toBeEnabled());
    await userEvent.click(screen.getByTestId("gate-policy-save"));

    await waitFor(() => expect(upsertTeamGatePolicy).toHaveBeenCalled());
    expect(upsertTeamGatePolicy.mock.calls[0][1]).toEqual({
      approval_required_statuses: null,
      epss_threshold: null,
      reachable_critical_only: null,
      malicious_blocks: null,
    });
  });

  it("keeps a zero threshold as a decision rather than an absence", async () => {
    getTeamGatePolicy.mockResolvedValue({
      id: "p1",
      organization_id: "o1",
      team_id: "team-1",
      name: null,
      epss_threshold: 0,
      reachable_critical_only: null,
      malicious_blocks: null,
      created_at: "2026-08-18T00:00:00Z",
      updated_at: "2026-08-18T00:00:00Z",
    });
    renderPanel();

    await waitFor(() =>
      expect(screen.getByTestId("gate-epss")).toHaveValue("0"),
    );
    await userEvent.click(screen.getByTestId("gate-policy-save"));

    await waitFor(() => expect(upsertTeamGatePolicy).toHaveBeenCalled());
    // Zero, not null: a policy that blocks on any score is a real policy, and
    // reading it back as "unset" would silently hand the decision back to the
    // organization.
    expect(upsertTeamGatePolicy.mock.calls[0][1].epss_threshold).toBe(0);
  });

  it("refuses to save a threshold outside the range the server accepts", async () => {
    getTeamGatePolicy.mockResolvedValue({
      id: "p1",
      organization_id: "o1",
      team_id: "team-1",
      name: null,
      epss_threshold: 0.5,
      reachable_critical_only: null,
      malicious_blocks: null,
      created_at: "2026-08-18T00:00:00Z",
      updated_at: "2026-08-18T00:00:00Z",
    });
    renderPanel();

    // The fetched row enables the field through an effect, so presence is not
    // readiness: typing into it the moment it renders types into a disabled box.
    const input = await screen.findByTestId("gate-epss");
    await waitFor(() => expect(input).toBeEnabled());
    await userEvent.clear(input);
    await userEvent.type(input, "5");

    expect(screen.getByTestId("gate-policy-save")).toBeDisabled();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("offers no controls to a grade that may read but not change", async () => {
    getTeamGatePolicy.mockResolvedValue({
      id: "p1",
      organization_id: "o1",
      team_id: "team-1",
      name: null,
      epss_threshold: 0.5,
      reachable_critical_only: null,
      malicious_blocks: null,
      created_at: "2026-08-18T00:00:00Z",
      updated_at: "2026-08-18T00:00:00Z",
    });
    renderPanel(false);

    await screen.findByTestId("gate-policy-readonly");
    expect(screen.queryByTestId("gate-policy-save")).not.toBeInTheDocument();
    expect(screen.getByTestId("gate-epss")).toBeDisabled();
  });

  it("offers the reset only when the team actually has a row", async () => {
    getTeamGatePolicy.mockRejectedValue(
      Object.assign(new Error("not found"), { status: 404, name: "ProblemError" }),
    );
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("gate-policy-save")).toBeEnabled());
    expect(screen.queryByTestId("gate-policy-reset")).not.toBeInTheDocument();
  });

  it("keeps the approval statuses a save would otherwise clear", async () => {
    // The panel replaces the whole row, so a field it does not send is a field
    // it deletes. This started as a real defect: the editor gained no control
    // for the new setting and saving any other field silently turned it off.
    getTeamGatePolicy.mockResolvedValue({
      id: "p1",
      organization_id: "o1",
      team_id: "team-1",
      name: null,
      epss_threshold: null,
      reachable_critical_only: null,
      malicious_blocks: null,
      approval_required_statuses: ["suppressed"],
      created_at: "2026-08-18T00:00:00Z",
      updated_at: "2026-08-18T00:00:00Z",
    });
    upsertTeamGatePolicy.mockResolvedValue({});
    renderPanel();

    await waitFor(() =>
      expect(screen.getByTestId("gate-approval-suppressed")).toBeChecked(),
    );
    await userEvent.click(screen.getByTestId("gate-policy-save"));

    await waitFor(() => expect(upsertTeamGatePolicy).toHaveBeenCalled());
    expect(
      upsertTeamGatePolicy.mock.calls[0][1].approval_required_statuses,
    ).toEqual(["suppressed"]);
  });

  it("hands the setting back to the organization when the override goes off", async () => {
    getTeamGatePolicy.mockResolvedValue({
      id: "p1",
      organization_id: "o1",
      team_id: "team-1",
      name: null,
      epss_threshold: null,
      reachable_critical_only: null,
      malicious_blocks: null,
      approval_required_statuses: ["suppressed"],
      created_at: "2026-08-18T00:00:00Z",
      updated_at: "2026-08-18T00:00:00Z",
    });
    upsertTeamGatePolicy.mockResolvedValue({});
    renderPanel();

    await waitFor(() =>
      expect(screen.getByTestId("gate-approval-suppressed")).toBeChecked(),
    );
    await userEvent.click(screen.getByTestId("gate-approval-override"));
    await userEvent.click(screen.getByTestId("gate-policy-save"));

    await waitFor(() => expect(upsertTeamGatePolicy).toHaveBeenCalled());
    expect(
      upsertTeamGatePolicy.mock.calls[0][1].approval_required_statuses,
    ).toBeNull();
  });
});
