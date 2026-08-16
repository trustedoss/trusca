/**
 * Getting started checklist (C2).
 *
 * The states worth pinning are the ones no CI gate renders: an organisation
 * with nothing in it, one part-way through, and one with no team at all. The
 * seeded stack every visual and a11y gate runs against always has projects,
 * so none of this is covered anywhere else.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OnboardingChecklist } from "@/features/dashboard/OnboardingChecklist";
import type { ProjectPublic } from "@/lib/projectsApi";
import { useAuthStore, type AuthUser } from "@/stores/authStore";
import { useUIStore } from "@/stores/uiStore";

const apiGet = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      get: (url: string, config?: { params?: Record<string, unknown> }) =>
        apiGet(url, config?.params ?? {}),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    },
  };
});

// useDemoMode reads /v1/health on mount. Stubbed so these tests do not fan
// out, and so the read-only branch is reachable at all.
const demoReadOnly = vi.fn(() => false);
vi.mock("@/hooks/useDemoMode", () => ({
  useDemoMode: () => ({ demoReadOnly: demoReadOnly() }),
}));

const POLICIES_URL = "/v1/license-policies";
const API_KEYS_URL = "/v1/api-keys";

/**
 * Answers the two count reads the checklist makes.
 *
 * Both paths and both envelopes were taken from a live server rather than
 * from the client that calls it: the policies and keys endpoints page with
 * `page_size`, not the `size` the projects and scans endpoints use, and a
 * mock that got that wrong would still satisfy a client written to match it.
 */
function respond({
  policies = 0,
  apiKeys = 0,
}: { policies?: number; apiKeys?: number } = {}) {
  return (url: string) => {
    if (url === POLICIES_URL) {
      return Promise.resolve({
        data: { items: [], total: policies, page: 1, page_size: 1 },
      });
    }
    if (url === API_KEYS_URL) {
      return Promise.resolve({
        data: { items: [], total: apiKeys, page: 1, page_size: 1 },
      });
    }
    return Promise.reject(new Error(`unexpected GET ${url}`));
  };
}

/**
 * Axios drops undefined parameters before they reach the wire, so the
 * clients' explicit `undefined` placeholders are not part of the request.
 * Removing them here lets the assertion be exact about what IS sent.
 */
function dropUndefined(params: Record<string, unknown>) {
  return Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== undefined),
  );
}

function project(overrides: Partial<ProjectPublic> = {}): ProjectPublic {
  return {
    id: "p-1",
    team_id: "t-1",
    name: "probe",
    slug: "probe",
    description: null,
    git_url: null,
    default_branch: null,
    visibility: "private",
    archived_at: null,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    latest_scan_status: null,
    severity_summary: null,
    license_category_summary: null,
    scan_count: 0,
    release_count: 0,
    last_scan_at: null,
    ...overrides,
  } as ProjectPublic;
}

const withTeam: AuthUser = {
  id: "u-1",
  email: "alice@example.com",
  displayName: "Alice",
  role: "team_admin",
  isActive: true,
  isSuperuser: false,
  teamId: "t-1",
  teams: [{ id: "t-1", name: "Platform", role: "team_admin" }],
};

function renderChecklist({
  projects = [],
  projectsLoaded = true,
}: { projects?: ProjectPublic[]; projectsLoaded?: boolean } = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <OnboardingChecklist
          projects={projects}
          projectsLoaded={projectsLoaded}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return client;
}

/**
 * Waits until both count reads have been issued.
 *
 * Counts calls per URL rather than in total: the card is absent for several
 * different reasons, and a total that happens to match would let a test that
 * never reached the second endpoint claim it had.
 */
async function settled() {
  await waitFor(() => {
    const urls = apiGet.mock.calls.map(([url]) => url as string);
    expect(urls).toContain(POLICIES_URL);
    expect(urls).toContain(API_KEYS_URL);
  });
}

describe("OnboardingChecklist", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useUIStore.setState({ onboardingDismissed: false, activeTeamId: null });
    useAuthStore.setState({
      user: withTeam,
      accessToken: "tok",
      status: "authenticated",
      isAuthenticated: true,
    });
    apiGet.mockReset();
    demoReadOnly.mockReturnValue(false);
  });
  afterEach(() => {
    useAuthStore.getState().reset();
    useUIStore.setState({ onboardingDismissed: false });
  });

  it("shows four unfinished steps to an organisation with nothing in it", async () => {
    apiGet.mockImplementation(respond());

    renderChecklist();

    const card = await screen.findByTestId("onboarding-checklist");
    const steps = within(card).getAllByTestId(/^onboarding-step-/);
    expect(steps).toHaveLength(4);
    expect(steps.every((step) => step.dataset.done === "false")).toBe(true);
    expect(screen.getByTestId("onboarding-progress")).toHaveTextContent(
      "0 of 4",
    );
  });

  it("points each step at the screen that does it", async () => {
    apiGet.mockImplementation(respond());

    renderChecklist();
    await screen.findByTestId("onboarding-checklist");

    // A checklist whose links go nowhere useful is a list of chores.
    expect(screen.getByTestId("onboarding-cta-project")).toHaveAttribute(
      "href",
      "/projects/new",
    );
    expect(screen.getByTestId("onboarding-cta-scan")).toHaveAttribute(
      "href",
      "/projects",
    );
    expect(screen.getByTestId("onboarding-cta-policy")).toHaveAttribute(
      "href",
      "/policies",
    );
    expect(screen.getByTestId("onboarding-cta-apiKey")).toHaveAttribute(
      "href",
      "/integrations",
    );
  });

  it("counts a project as registered but not yet scanned", async () => {
    // The distinction the whole second step exists for: a registered project
    // shows nothing at all until something has scanned it.
    apiGet.mockImplementation(respond());

    renderChecklist({ projects: [project()] });

    await screen.findByTestId("onboarding-checklist");
    expect(screen.getByTestId("onboarding-step-project").dataset.done).toBe(
      "true",
    );
    expect(screen.getByTestId("onboarding-step-scan").dataset.done).toBe(
      "false",
    );
  });

  it("treats a scan as done only once one has succeeded", async () => {
    // `scan_count` counts attempts; a queued or failed one has produced no
    // component list, which is what the step is actually about.
    apiGet.mockImplementation(respond());

    renderChecklist({
      projects: [project({ scan_count: 3, release_count: 0 })],
    });

    await screen.findByTestId("onboarding-checklist");
    expect(screen.getByTestId("onboarding-step-scan").dataset.done).toBe(
      "false",
    );
  });

  it("ticks the scan step for a succeeded scan on any project", async () => {
    apiGet.mockImplementation(respond());

    renderChecklist({
      projects: [
        project({ id: "p-1" }),
        project({ id: "p-2", scan_count: 1, release_count: 1 }),
      ],
    });

    await screen.findByTestId("onboarding-checklist");
    expect(screen.getByTestId("onboarding-step-scan").dataset.done).toBe(
      "true",
    );
  });

  it("reads the policy and key steps from their own endpoints", async () => {
    apiGet.mockImplementation(respond({ policies: 1, apiKeys: 2 }));

    renderChecklist();

    await screen.findByTestId("onboarding-checklist");
    expect(screen.getByTestId("onboarding-step-policy").dataset.done).toBe(
      "true",
    );
    expect(screen.getByTestId("onboarding-step-apiKey").dataset.done).toBe(
      "true",
    );
    expect(screen.getByTestId("onboarding-progress")).toHaveTextContent(
      "2 of 4",
    );
    // One row each, because only the totals are wanted. Asking for a default
    // page would pull every policy and every key into a dashboard render.
    for (const url of [POLICIES_URL, API_KEYS_URL]) {
      const call = apiGet.mock.calls.find(([called]) => called === url);
      expect(call, `no request was made to ${url}`).toBeDefined();
      // `toEqual`, not `toMatchObject`: a partial match lets an extra
      // parameter through, and the one that matters is `include_revoked`.
      // Adding it would quietly change the step from "a key exists" to "a key
      // once existed" while every other assertion in this file stayed green.
      expect(dropUndefined(call?.[1] as Record<string, unknown>)).toEqual({
        page: 1,
        page_size: 1,
      });
    }
  });

  it("disappears once every step is done", async () => {
    apiGet.mockImplementation(respond({ policies: 1, apiKeys: 1 }));

    renderChecklist({
      projects: [project({ scan_count: 1, release_count: 1 })],
    });

    await settled();
    await waitFor(() => {
      expect(screen.queryByTestId("onboarding-checklist")).toBeNull();
    });
  });

  it("draws nothing while a step's state is still unknown", async () => {
    // An unticked box asserts the step is outstanding. A query that has not
    // answered supports no such claim, and a reader who set a policy last
    // week should not be told they have none.
    apiGet.mockImplementation(() => new Promise(() => {}));

    renderChecklist();

    await waitFor(() => {
      expect(apiGet).toHaveBeenCalled();
    });
    expect(screen.queryByTestId("onboarding-checklist")).toBeNull();
  });

  it("draws nothing when a count request fails", async () => {
    apiGet.mockRejectedValue(new Error("boom"));

    renderChecklist();

    await settled();
    expect(screen.queryByTestId("onboarding-checklist")).toBeNull();
  });

  it("waits for the projects query too", async () => {
    apiGet.mockImplementation(respond());

    renderChecklist({ projectsLoaded: false });

    await settled();
    expect(screen.queryByTestId("onboarding-checklist")).toBeNull();
  });

  it("stays dismissed", async () => {
    apiGet.mockImplementation(respond());
    const user = userEvent.setup();

    renderChecklist();
    await screen.findByTestId("onboarding-checklist");
    await user.click(screen.getByTestId("onboarding-dismiss"));

    expect(screen.queryByTestId("onboarding-checklist")).toBeNull();
    // Persisted, not just hidden for this render: an organisation that has
    // been running for a year should say "not for me" once, not once a day.
    expect(useUIStore.getState().onboardingDismissed).toBe(true);
    expect(window.localStorage.getItem("trustedoss-ui")).toContain(
      '"onboardingDismissed":true',
    );
  });

  it("offers no action on a deployment that refuses writes", async () => {
    // A demo deployment answers 403 to every write, so a button here would be
    // an invitation to a refusal. The steps still show their state.
    demoReadOnly.mockReturnValue(true);
    apiGet.mockImplementation(respond());

    renderChecklist();

    await screen.findByTestId("onboarding-checklist");
    expect(screen.queryAllByTestId(/^onboarding-cta-/)).toHaveLength(0);
  });

  it("marks a done step by more than its colour", async () => {
    apiGet.mockImplementation(respond({ policies: 1 }));

    renderChecklist();

    await screen.findByTestId("onboarding-checklist");
    const done = screen.getByTestId("onboarding-step-policy");
    const pending = screen.getByTestId("onboarding-step-apiKey");
    // Colour is never the only carrier of state: the label of a finished step
    // is struck through as well as ticked.
    expect(done.innerHTML).toContain("line-through");
    expect(pending.innerHTML).not.toContain("line-through");
  });

  it("stops asking once the card is dismissed for good", async () => {
    // Two requests per dashboard load, for ever, on behalf of a card that
    // will never be drawn again.
    useUIStore.setState({ onboardingDismissed: true });
    apiGet.mockImplementation(respond());

    renderChecklist();

    await waitFor(() => {
      expect(screen.queryByTestId("onboarding-checklist")).toBeNull();
    });
    expect(apiGet).not.toHaveBeenCalled();
  });

  it("offers no action a user with no team could take", async () => {
    // Every one of the four needs a team: a project belongs to one, a policy
    // is written per team, a key is scoped to a team or to a project in one.
    // Four buttons leading to four refusals would be worse than none.
    useAuthStore.setState({
      user: { ...withTeam, teamId: null, teams: [] },
      accessToken: "tok",
      status: "authenticated",
      isAuthenticated: true,
    });
    apiGet.mockImplementation(respond());

    renderChecklist();

    const card = await screen.findByTestId("onboarding-checklist");
    expect(within(card).getAllByTestId(/^onboarding-step-/)).toHaveLength(4);
    expect(screen.queryAllByTestId(/^onboarding-cta-/)).toHaveLength(0);
    expect(card.textContent).toContain("do not belong to a team");
  });
});
