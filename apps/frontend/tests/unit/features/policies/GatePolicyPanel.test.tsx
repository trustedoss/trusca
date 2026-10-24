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
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLayoutEffect, useRef, useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GatePolicyPanel } from "@/features/policies/GatePolicyPanel";
import { teamGatePolicyQueryKey } from "@/features/policies/useGatePolicies";
import { GATE_POLICY_PROBLEM_TYPES } from "@/lib/gatePoliciesApi";
import { ProblemError } from "@/lib/problem";

const getTeamGatePolicy = vi.fn();
const upsertTeamGatePolicy = vi.fn();
const deleteTeamGatePolicy = vi.fn();
const getEpssAvailability = vi.fn();

/** A team row with every axis inherited, for the tests that only vary one. */
const POLICY = {
  id: "p1",
  organization_id: "o1",
  team_id: "team-1",
  name: null,
  epss_threshold: null,
  reachable_critical_only: null,
  malicious_blocks: null,
  approval_required_statuses: null,
  created_at: "2026-08-18T00:00:00Z",
  updated_at: "2026-08-18T00:00:00Z",
};

// Spread the real module rather than listing its exports: a stub that names
// them one by one goes stale the moment the module grows a constant, and it
// fails as an unrelated crash rather than as a missing mock.
// The stub returns a bare row, and the wrapper pairs it with a tag derived
// from the row the way a server would: a different row is a different tag.
function tagOf(row: { id: string; updated_at: string }): string {
  return `"${row.id}@${row.updated_at}"`;
}

vi.mock("@/lib/gatePoliciesApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/gatePoliciesApi")>()),
  getTeamGatePolicy: async (...args: unknown[]) => {
    const row = (await getTeamGatePolicy(...args)) as { id: string; updated_at: string };
    return { policy: row, etag: tagOf(row) };
  },
  upsertTeamGatePolicy: async (...args: unknown[]) => {
    const row = (await upsertTeamGatePolicy(...args)) as { id: string; updated_at: string };
    return { policy: row, etag: tagOf(row) };
  },
  deleteTeamGatePolicy: (...args: unknown[]) => deleteTeamGatePolicy(...args),
  upsertOrgGatePolicy: vi.fn(),
  getEffectiveGatePolicy: vi.fn(),
  getEpssAvailability: (...args: unknown[]) => getEpssAvailability(...args),
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

// A real ProblemError, not a look-alike: the hook turns only an actual 404
// ProblemError into "no row", and a stand-in would silently become a failed
// read instead.
function problem(status: number, type?: string): ProblemError {
  return new ProblemError("gate policy", {
    status,
    title: "error",
    detail: "error",
    problem: type === undefined ? null : { type, title: "error", status, detail: "error" },
  });
}

/** The team has no row of its own: the only 404 that means "follows the organization". */
function notSet(): ProblemError {
  return problem(404, GATE_POLICY_PROBLEM_TYPES.not_set);
}

function conflict(): ProblemError {
  return problem(409, GATE_POLICY_PROBLEM_TYPES.conflict);
}

/**
 * Press whatever Save button is on screen, if any. The guard tests attempt the
 * save rather than only checking the button is gone, so on a panel that still
 * offers Save they fail on the mutation call itself.
 */
async function pressEverySave(): Promise<void> {
  for (const button of screen.queryAllByTestId("gate-policy-save")) {
    await userEvent.click(button);
  }
  await new Promise((resolve) => setTimeout(resolve, 0));
}

/**
 * Switches the panel between teams and presses Save on the very first frame
 * after each switch, before any effect has run. That frame is where a draft
 * seeded for the previous team could still be on screen.
 */
function TeamSwitcher() {
  const [teamId, setTeamId] = useState("team-1");
  const first = useRef(true);
  useLayoutEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    for (const button of document.querySelectorAll<HTMLButtonElement>(
      '[data-testid="gate-policy-save"]',
    )) {
      button.click();
    }
  }, [teamId]);
  return (
    <>
      <button type="button" onClick={() => setTeamId("team-2")}>
        switch-team
      </button>
      <GatePolicyPanel teamId={teamId} canEdit />
    </>
  );
}

describe("GatePolicyPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    upsertTeamGatePolicy.mockResolvedValue({});
    deleteTeamGatePolicy.mockResolvedValue(undefined);
    // Healthy by default: the caveat is the exception, not the baseline.
    getEpssAvailability.mockResolvedValue({
      available: true,
      refresh_enabled: true,
      scored_cves: 1200,
      last_synced_at: "2026-09-03T02:20:00Z",
    });
  });

  it("sends null for a field the team has not taken over", async () => {
    getTeamGatePolicy.mockRejectedValue(notSet());
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("gate-policy-save")).toBeEnabled());
    await userEvent.click(screen.getByTestId("gate-policy-save"));

    await waitFor(() => expect(upsertTeamGatePolicy).toHaveBeenCalled());
    expect(upsertTeamGatePolicy.mock.calls[0][1]).toEqual({
      name: null,
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
    getTeamGatePolicy.mockRejectedValue(notSet());
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

  it("warns that a threshold decides nothing when the deployment has no EPSS", async () => {
    // ER43 condition 1: the person who sets the threshold is not the one
    // reading CI output, so without this they never learn it is inert.
    getEpssAvailability.mockResolvedValue({
      available: false,
      refresh_enabled: false,
      scored_cves: 0,
      last_synced_at: null,
    });
    getTeamGatePolicy.mockResolvedValue({ ...POLICY, epss_threshold: 0.5 });
    renderPanel();

    await waitFor(() => {
      expect(screen.getByTestId("gate-epss-unavailable")).toBeInTheDocument();
    });
    expect(screen.getByTestId("gate-epss-unavailable").textContent).toContain(
      "no_data_disabled",
    );
  });

  it("distinguishes a stale sync from one that was never switched on", async () => {
    // Different problem, different fix: one is an env var, the other is a
    // feed that stopped landing.
    getEpssAvailability.mockResolvedValue({
      available: false,
      refresh_enabled: true,
      scored_cves: 42,
      last_synced_at: "2026-08-01T02:20:00Z",
    });
    getTeamGatePolicy.mockResolvedValue({ ...POLICY, epss_threshold: 0.5 });
    renderPanel();

    await waitFor(() => {
      expect(screen.getByTestId("gate-epss-unavailable")).toBeInTheDocument();
    });
    expect(screen.getByTestId("gate-epss-unavailable").textContent).toContain(
      "no_data_stale",
    );
  });

  it("says nothing when the deployment has usable EPSS data", async () => {
    getTeamGatePolicy.mockResolvedValue({ ...POLICY, epss_threshold: 0.5 });
    renderPanel();

    await waitFor(() => {
      expect(screen.getByTestId("gate-epss")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("gate-epss-unavailable"),
    ).not.toBeInTheDocument();
  });

  it("says nothing when this team sets no threshold of its own", async () => {
    // The caveat belongs next to a threshold somebody set. On an inherited
    // row it would be noise on a screen the reader cannot act from.
    getEpssAvailability.mockResolvedValue({
      available: false,
      refresh_enabled: false,
      scored_cves: 0,
      last_synced_at: null,
    });
    getTeamGatePolicy.mockResolvedValue({ ...POLICY, epss_threshold: null });
    renderPanel();

    await waitFor(() => {
      expect(screen.getByTestId("gate-epss")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("gate-epss-unavailable"),
    ).not.toBeInTheDocument();
  });

  // Save replaces the team's row wholesale. A draft that was never seeded from
  // a successful read is the blank default, and saving it would wipe the real
  // policy, so the controls must not exist until the read succeeds.
  it("cannot save while the policy is still loading", async () => {
    getTeamGatePolicy.mockReturnValue(new Promise(() => {}));
    renderPanel();

    await waitFor(() => expect(getTeamGatePolicy).toHaveBeenCalled());
    await pressEverySave();
    expect(upsertTeamGatePolicy).not.toHaveBeenCalled();
    expect(screen.getByTestId("gate-policy-loading")).toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });

  it("cannot save after the read fails, and saves the real policy once a retry succeeds", async () => {
    getTeamGatePolicy.mockRejectedValueOnce(problem(403));
    renderPanel();

    await waitFor(() => expect(getTeamGatePolicy).toHaveBeenCalled());
    await pressEverySave();
    expect(upsertTeamGatePolicy).not.toHaveBeenCalled();
    await screen.findByTestId("gate-policy-error");
    await pressEverySave();
    expect(upsertTeamGatePolicy).not.toHaveBeenCalled();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();

    getTeamGatePolicy.mockResolvedValue({ ...POLICY, epss_threshold: 0.3, malicious_blocks: false });
    await userEvent.click(screen.getByTestId("gate-policy-retry"));
    await userEvent.click(await screen.findByTestId("gate-policy-save"));

    await waitFor(() => expect(upsertTeamGatePolicy).toHaveBeenCalledTimes(1));
    expect(upsertTeamGatePolicy.mock.calls[0][1]).toEqual({
      name: null,
      epss_threshold: 0.3,
      reachable_critical_only: null,
      malicious_blocks: false,
      approval_required_statuses: null,
    });
  });

  it("gives every switch an accessible name", async () => {
    getTeamGatePolicy.mockResolvedValue(POLICY);
    renderPanel();

    await screen.findByTestId("gate-policy-save");
    for (const name of [
      "gate.epss.override",
      "gate.reachable.override",
      "gate.reachable.label",
      "gate.malicious.override",
      "gate.malicious.label",
      "gate.approval.override",
    ]) {
      expect(screen.getByRole("switch", { name })).toBeInTheDocument();
    }
    // The name points at the right control, not merely at some switch.
    expect(screen.getByRole("switch", { name: "gate.reachable.label" })).toHaveAttribute(
      "data-testid",
      "gate-reachable",
    );
    expect(screen.getByRole("switch", { name: "gate.malicious.label" })).toHaveAttribute(
      "data-testid",
      "gate-malicious",
    );
  });

  it("never saves the previous team's draft after switching to a cached team", async () => {
    const team1 = { ...POLICY, epss_threshold: 0.9, malicious_blocks: false };
    const team2 = { ...POLICY, id: "p2", team_id: "team-2", epss_threshold: 0.2 };
    getTeamGatePolicy.mockImplementation((teamId: string) =>
      Promise.resolve(teamId === "team-2" ? team2 : team1),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // Already cached, so the query for team-2 succeeds on the first render.
    client.setQueryData(teamGatePolicyQueryKey("team-2"), { policy: team2, etag: tagOf(team2) });
    render(
      <QueryClientProvider client={client}>
        <TeamSwitcher />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("gate-epss")).toHaveValue("0.9"));

    await userEvent.click(screen.getByRole("button", { name: "switch-team" }));
    await waitFor(() => expect(screen.getByTestId("gate-epss")).toHaveValue("0.2"));
    expect(screen.getByTestId("gate-malicious-override")).not.toBeChecked();
    await userEvent.click(screen.getByTestId("gate-policy-save"));

    await waitFor(() => expect(upsertTeamGatePolicy).toHaveBeenCalled());
    // Every save went to team-2 with team-2's values. A save of team-1's
    // draft would carry 0.9 and malicious_blocks false into team-2's row.
    for (const [teamId, payload, basis] of upsertTeamGatePolicy.mock.calls) {
      expect(teamId).toBe("team-2");
      // And with team-2's tag: team-1's would be refused, or worse, match a
      // row it was never read from.
      expect(basis).toEqual({ etag: tagOf(team2) });
      expect(payload).toEqual({
        name: null,
        epss_threshold: 0.2,
        reachable_critical_only: null,
        malicious_blocks: null,
        approval_required_statuses: null,
      });
    }
  });

  // The app's client keeps a read fresh for 30 s, so a policy is not
  // refetched on return unless the save invalidated that team's key. A save
  // that finishes after the user moved to another team must still invalidate
  // the team it was for, not the team now on screen.
  it("refreshes the saved team's policy even when the save finishes after a team switch", async () => {
    let team1Epss = 0.9;
    getTeamGatePolicy.mockImplementation((teamId: string) =>
      Promise.resolve(
        teamId === "team-2"
          ? { ...POLICY, id: "p2", team_id: "team-2", epss_threshold: 0.2 }
          : { ...POLICY, epss_threshold: team1Epss },
      ),
    );
    let finishSave: (value: unknown) => void = () => {};
    upsertTeamGatePolicy.mockReturnValue(
      new Promise((resolve) => {
        finishSave = resolve;
      }),
    );
    function Switcher() {
      const [teamId, setTeamId] = useState("team-1");
      return (
        <>
          <button type="button" onClick={() => setTeamId("team-1")}>
            team-1
          </button>
          <button type="button" onClick={() => setTeamId("team-2")}>
            team-2
          </button>
          <GatePolicyPanel teamId={teamId} canEdit />
        </>
      );
    }
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 30_000 } },
    });
    render(
      <QueryClientProvider client={client}>
        <Switcher />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("gate-epss")).toHaveValue("0.9"));
    await userEvent.clear(screen.getByTestId("gate-epss"));
    await userEvent.type(screen.getByTestId("gate-epss"), "0.5");
    await userEvent.click(screen.getByTestId("gate-policy-save"));
    expect(upsertTeamGatePolicy).toHaveBeenCalledWith(
      "team-1",
      expect.objectContaining({ epss_threshold: 0.5 }),
      expect.anything(),
    );

    await userEvent.click(screen.getByRole("button", { name: "team-2" }));
    await waitFor(() => expect(screen.getByTestId("gate-epss")).toHaveValue("0.2"));
    team1Epss = 0.5;
    await act(async () => {
      finishSave({ ...POLICY, epss_threshold: 0.5 });
    });

    await userEvent.click(screen.getByRole("button", { name: "team-1" }));
    await waitFor(() => expect(screen.getByTestId("gate-epss")).toHaveValue("0.5"));
  });

  it("keeps the policy's name, which the editor has no field for", async () => {
    // The save replaces the row, so a name the payload leaves out is stored
    // as NULL: every save from this panel used to erase it.
    getTeamGatePolicy.mockResolvedValue({ ...POLICY, name: "Release gate" });
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("gate-policy-save")).toBeEnabled());
    await userEvent.click(screen.getByTestId("gate-policy-save"));

    await waitFor(() => expect(upsertTeamGatePolicy).toHaveBeenCalled());
    expect(upsertTeamGatePolicy.mock.calls[0][1].name).toBe("Release gate");
  });

  it("sends back the tag of the row it read", async () => {
    getTeamGatePolicy.mockResolvedValue(POLICY);
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("gate-policy-save")).toBeEnabled());
    await userEvent.click(screen.getByTestId("gate-policy-save"));

    await waitFor(() => expect(upsertTeamGatePolicy).toHaveBeenCalled());
    expect(upsertTeamGatePolicy.mock.calls[0][2]).toEqual({ etag: tagOf(POLICY) });
  });

  it("asks to create the row only if nobody did meanwhile, when it read none", async () => {
    getTeamGatePolicy.mockRejectedValue(notSet());
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("gate-policy-save")).toBeEnabled());
    await userEvent.click(screen.getByTestId("gate-policy-save"));

    await waitFor(() => expect(upsertTeamGatePolicy).toHaveBeenCalled());
    expect(upsertTeamGatePolicy.mock.calls[0][2]).toEqual({ etag: null });
  });

  it("treats a plain 404 as a failed read, not as a team with no row", async () => {
    // A team that is gone, or hidden from this user, answers 404 without the
    // not-set type. Reading that as "follows the organization" would offer an
    // editable form for a team the user cannot reach.
    getTeamGatePolicy.mockRejectedValue(problem(404, "about:blank"));
    renderPanel();

    await screen.findByTestId("gate-policy-error");
    await pressEverySave();
    expect(upsertTeamGatePolicy).not.toHaveBeenCalled();
  });

  it("says somebody else saved first and reloads the current policy on request", async () => {
    getTeamGatePolicy.mockResolvedValue({ ...POLICY, epss_threshold: 0.5 });
    upsertTeamGatePolicy.mockRejectedValue(conflict());
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("gate-epss")).toHaveValue("0.5"));
    await userEvent.clear(screen.getByTestId("gate-epss"));
    await userEvent.type(screen.getByTestId("gate-epss"), "0.9");
    await userEvent.click(screen.getByTestId("gate-policy-save"));

    const alert = await screen.findByTestId("gate-policy-conflict");
    expect(alert.textContent).toContain("gate.conflict");
    expect(screen.queryByTestId("gate-policy-error")).not.toBeInTheDocument();

    // What the other person saved, under a new tag.
    const theirs = {
      ...POLICY,
      epss_threshold: 0.7,
      updated_at: "2026-08-19T00:00:00Z",
    };
    getTeamGatePolicy.mockResolvedValue(theirs);
    upsertTeamGatePolicy.mockResolvedValue(theirs);
    await userEvent.click(screen.getByTestId("gate-policy-conflict-reload"));

    await waitFor(() => expect(screen.getByTestId("gate-epss")).toHaveValue("0.7"));
    expect(screen.queryByTestId("gate-policy-conflict")).not.toBeInTheDocument();

    await userEvent.click(screen.getByTestId("gate-policy-save"));
    await waitFor(() => expect(upsertTeamGatePolicy).toHaveBeenCalledTimes(2));
    expect(upsertTeamGatePolicy.mock.calls[1][2]).toEqual({ etag: tagOf(theirs) });
  });

  it("bases a second quick save on the first save's tag, not on the stale read", async () => {
    // A server that refuses any save not based on its current row, the way
    // the API does, and a refetch that never lands, so the only way the
    // second save can know the new tag is from the first save's response.
    let server: Omit<typeof POLICY, "epss_threshold"> & { epss_threshold: number | null } = {
      ...POLICY,
      epss_threshold: 0.5,
    };
    let version = 0;
    getTeamGatePolicy.mockResolvedValueOnce(server);
    getTeamGatePolicy.mockReturnValue(new Promise(() => {}));
    upsertTeamGatePolicy.mockImplementation(
      (_teamId: string, payload: { epss_threshold: number | null }, basis: { etag: string }) => {
        if (basis.etag !== tagOf(server)) return Promise.reject(conflict());
        version += 1;
        server = {
          ...server,
          epss_threshold: payload.epss_threshold,
          updated_at: `2026-08-18T00:00:0${version}Z`,
        };
        return Promise.resolve(server);
      },
    );
    renderPanel();

    const input = await screen.findByTestId("gate-epss");
    await waitFor(() => expect(input).toHaveValue("0.5"));
    await userEvent.clear(input);
    await userEvent.type(input, "0.6");
    await userEvent.click(screen.getByTestId("gate-policy-save"));
    await waitFor(() => expect(upsertTeamGatePolicy).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByTestId("gate-policy-save")).toBeEnabled());
    const afterFirst = server;

    await userEvent.clear(screen.getByTestId("gate-epss"));
    await userEvent.type(screen.getByTestId("gate-epss"), "0.7");
    await userEvent.click(screen.getByTestId("gate-policy-save"));
    await waitFor(() => expect(upsertTeamGatePolicy).toHaveBeenCalledTimes(2));

    expect(upsertTeamGatePolicy.mock.calls[0][2]).toEqual({ etag: tagOf(POLICY) });
    expect(upsertTeamGatePolicy.mock.calls[1][2]).toEqual({ etag: tagOf(afterFirst) });
    await waitFor(() => expect(server.epss_threshold).toBe(0.7));
    expect(screen.queryByTestId("gate-policy-conflict")).not.toBeInTheDocument();
  });

  it("does not call any other failed save a conflict", async () => {
    getTeamGatePolicy.mockResolvedValue(POLICY);
    upsertTeamGatePolicy.mockRejectedValue(problem(409, "about:blank"));
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("gate-policy-save")).toBeEnabled());
    await userEvent.click(screen.getByTestId("gate-policy-save"));

    await waitFor(() => expect(upsertTeamGatePolicy).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByTestId("gate-policy-conflict")).not.toBeInTheDocument();
  });
});
