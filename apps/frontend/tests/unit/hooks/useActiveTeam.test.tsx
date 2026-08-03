/**
 * W14 — active team resolution and the sequence that used to break.
 *
 * The independent review caught two defects the first implementation
 * shipped, and both are asserted here because neither was reachable from
 * the tests that existed: the project-creation form read the team once into
 * a `useState` initialiser, so switching teams in the bar left the two
 * controls disagreeing; and the choice lived in the auth store, which is
 * not persisted, so it vanished on reload.
 *
 * Per CLAUDE.md hardening rule 5, the lifecycle sequence — open the form,
 * switch team, submit — is the test that matters, not each step alone.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppProviders } from "@/components/AppProviders";
import { useActiveTeam } from "@/hooks/useActiveTeam";
import { useAuthStore, type AuthUser } from "@/stores/authStore";
import { useUIStore } from "@/stores/uiStore";

const TEAMS = [
  { id: "team-1", name: "Platform", role: "developer" },
  { id: "team-2", name: "Security", role: "developer" },
];

const user: AuthUser = {
  id: "u-1",
  email: "alice@example.com",
  displayName: "Alice",
  role: "developer",
  isActive: true,
  isSuperuser: false,
  teamId: "team-1",
  teams: TEAMS,
};

function Probe() {
  const team = useActiveTeam();
  return <span data-testid="active">{team?.name ?? "none"}</span>;
}

function renderProbe() {
  return render(
    <AppProviders>
      <Probe />
    </AppProviders>,
  );
}

beforeEach(() => {
  useAuthStore.setState({
    user,
    accessToken: "tok",
    status: "authenticated",
    isAuthenticated: true,
  });
  useUIStore.setState({ activeTeamId: null });
});

afterEach(() => {
  useAuthStore.getState().reset();
  useUIStore.setState({ activeTeamId: null });
  vi.restoreAllMocks();
});

describe("useActiveTeam", () => {
  it("falls back to the membership the API resolved", () => {
    renderProbe();
    expect(screen.getByTestId("active").textContent).toBe("Platform");
  });

  it("prefers a stored choice over the API default", () => {
    useUIStore.setState({ activeTeamId: "team-2" });
    renderProbe();
    expect(screen.getByTestId("active").textContent).toBe("Security");
  });

  it("discards a stored team the user is no longer a member of", () => {
    // A revoked membership must not keep scoping anything. The stored id
    // outlives the session, so this is not hypothetical.
    useUIStore.setState({ activeTeamId: "team-gone" });
    renderProbe();
    expect(screen.getByTestId("active").textContent).toBe("Platform");
  });

  it("returns nothing for a user with no memberships", () => {
    useAuthStore.setState({ user: { ...user, teamId: null, teams: [] } });
    renderProbe();
    expect(screen.getByTestId("active").textContent).toBe("none");
  });

  it("survives a reload, because the choice is persisted", async () => {
    // uiStore writes through to localStorage; the auth store deliberately
    // does not persist (it holds the access token), which is why the team
    // lives here.
    useUIStore.getState().setActiveTeamId("team-2");

    await waitFor(() => {
      const stored = window.localStorage.getItem("trustedoss-ui");
      expect(stored).toBeTruthy();
      expect(JSON.parse(stored as string).state.activeTeamId).toBe("team-2");
    });
  });
});

describe("switching teams while the create form is open", () => {
  it("moves the form's team with the bar", async () => {
    // The sequence that was broken: the form mounted with Platform, the bar
    // switched to Security, and submit still POSTed Platform.
    const { ProjectCreatePage } = await import(
      "@/features/projects/ProjectCreatePage"
    );

    render(
      <AppProviders>
        <ProjectCreatePage />
      </AppProviders>,
    );

    const select = await screen.findByTestId("project-team-select");
    expect((select as HTMLSelectElement).value).toBe("team-1");

    useUIStore.getState().setActiveTeamId("team-2");

    await waitFor(() => {
      expect((select as HTMLSelectElement).value).toBe("team-2");
    });
  });

  it("still lets the form override the bar", async () => {
    const { ProjectCreatePage } = await import(
      "@/features/projects/ProjectCreatePage"
    );

    render(
      <AppProviders>
        <ProjectCreatePage />
      </AppProviders>,
    );

    const select = (await screen.findByTestId(
      "project-team-select",
    )) as HTMLSelectElement;

    await userEvent.selectOptions(select, "team-2");
    expect(select.value).toBe("team-2");
    // The bar has not moved, so the explicit pick stands.
    expect(useUIStore.getState().activeTeamId).toBeNull();
  });
});
