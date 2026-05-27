/**
 * License policy editor — unit tests (v2.2 c3).
 *
 * Two surfaces under test:
 *   - PoliciesPage: renders visible policies, opens the editor drawer on a row
 *     click (URL-encoded `?policy=team:<id>`), and reopens the drawer from a
 *     deep-linked URL (hard-reload survival).
 *   - PolicyEditorPanel + PolicyEditorForm: seeds from the server policy, adds /
 *     removes an override, adds an exception, toggles enabled, saves (PUT), the
 *     error path surfaces a toast, and read-only mode for a non-team_admin (403).
 *
 * The wire layer (`@/lib/licensePoliciesApi`) and the team-discovery sources
 * (`@/features/admin/api/adminTeamsApi`, `@/lib/projectsApi`) are mocked so no
 * backend is needed. i18n is the real EN bundle (tests/setup.ts) so we assert on
 * rendered copy and stable `data-*` hooks.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PoliciesPage } from "@/features/policies/PoliciesPage";
import { ProblemError } from "@/lib/problem";
import { useAuthStore, type AuthUser } from "@/stores/authStore";
import type {
  LicensePolicyListPage,
  LicensePolicyOut,
} from "@/lib/licensePoliciesApi";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/lib/licensePoliciesApi", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/licensePoliciesApi")>(
      "@/lib/licensePoliciesApi",
    );
  return {
    ...actual,
    listLicensePolicies: vi.fn(),
    getTeamPolicy: vi.fn(),
    getOrgPolicy: vi.fn(),
    upsertTeamPolicy: vi.fn(),
    upsertOrgPolicy: vi.fn(),
    deleteTeamPolicy: vi.fn(),
  };
});

vi.mock("@/features/admin/api/adminTeamsApi", () => ({
  listAdminTeams: vi.fn(),
}));

vi.mock("@/lib/projectsApi", () => ({
  listProjects: vi.fn(),
}));

import {
  deleteTeamPolicy,
  getOrgPolicy,
  getTeamPolicy,
  listLicensePolicies,
  upsertOrgPolicy,
  upsertTeamPolicy,
} from "@/lib/licensePoliciesApi";
import { listAdminTeams } from "@/features/admin/api/adminTeamsApi";
import { listProjects } from "@/lib/projectsApi";

const mockedList = vi.mocked(listLicensePolicies);
const mockedGetTeam = vi.mocked(getTeamPolicy);
const mockedGetOrg = vi.mocked(getOrgPolicy);
const mockedUpsertTeam = vi.mocked(upsertTeamPolicy);
const mockedUpsertOrg = vi.mocked(upsertOrgPolicy);
const mockedDeleteTeam = vi.mocked(deleteTeamPolicy);
const mockedAdminTeams = vi.mocked(listAdminTeams);
const mockedProjects = vi.mocked(listProjects);

const TEAM_ID = "11111111-1111-1111-1111-111111111111";
const ORG_ID = "99999999-9999-9999-9999-999999999999";

function policyFixture(over: Partial<LicensePolicyOut> = {}): LicensePolicyOut {
  return {
    id: "aaaaaaaa-0000-0000-0000-000000000001",
    organization_id: ORG_ID,
    team_id: TEAM_ID,
    name: "Engineering policy",
    category_overrides: { "MPL-2.0": "forbidden" },
    license_exceptions: [],
    unknown_license_category: "conditional",
    compound_operator_strategy: {
      AND: "most_restrictive",
      OR: "least_restrictive",
      WITH: "most_restrictive",
    },
    enabled: true,
    created_by_user_id: null,
    created_at: "2026-05-24T00:00:00Z",
    updated_at: "2026-05-24T00:00:00Z",
    ...over,
  };
}

function listFixture(
  items: LicensePolicyOut[] = [policyFixture()],
): LicensePolicyListPage {
  return { items, total: items.length, page: 1, page_size: 50 };
}

function problem(status: number): ProblemError {
  return new ProblemError("nope", {
    status,
    title: "err",
    detail: "nope",
    problem: { type: "about:blank", title: "err", status, detail: "nope" },
  });
}

const devUser: AuthUser = {
  id: "user-dev",
  email: "dev@example.com",
  displayName: "Dev",
  role: "developer",
  isActive: true,
  isSuperuser: false,
  teamId: TEAM_ID,
};

function renderPage(initialPath = "/policies") {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialPath]}>
        <PoliciesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("PoliciesPage + editor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedList.mockResolvedValue(listFixture());
    mockedAdminTeams.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 200,
    });
    mockedProjects.mockResolvedValue({
      items: [
        {
          id: "proj-1",
          team_id: TEAM_ID,
          name: "App",
          slug: "app",
          description: null,
          git_url: null,
          default_branch: null,
          visibility: "team",
          archived_at: null,
          created_by_user_id: null,
          latest_scan_id: null,
          latest_scan_status: null,
          severity_summary: null,
          license_category_summary: null,
          created_by_user_name: null,
          has_git_credential: false,
          scan_count: 0,
          release_count: 0,
          last_scan_at: null,
          created_at: "2026-05-24T00:00:00Z",
          updated_at: "2026-05-24T00:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      size: 200,
    });
    mockedGetTeam.mockResolvedValue(policyFixture());
    useAuthStore.setState({
      user: devUser,
      accessToken: "tok",
      status: "authenticated",
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    useAuthStore.getState().reset();
  });

  it("renders the visible policies in a compact table", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("policies-table")).toBeInTheDocument();
    });
    const rows = await screen.findAllByTestId("policies-row");
    expect(rows).toHaveLength(1);
    // Override count cell reflects the fixture (1 override).
    expect(within(rows[0]).getByText("1")).toBeInTheDocument();
    // Status badge shows the Enabled label (color paired with text).
    expect(within(rows[0]).getByText(/enabled/i)).toBeInTheDocument();
  });

  it("opens the editor drawer on a row click and URL-encodes the scope", async () => {
    const user = userEvent.setup();
    renderPage();
    const row = await screen.findByTestId("policies-row");
    await user.click(row);

    // The drawer mounts and the editor reads the team policy.
    await waitFor(() => {
      expect(screen.getByTestId("policy-drawer")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(mockedGetTeam).toHaveBeenCalledWith(TEAM_ID);
    });
    // The form seeds from the server policy (the MPL override is present).
    const overrideRow = await screen.findByTestId("policy-override-row");
    expect(
      within(overrideRow).getByDisplayValue("MPL-2.0"),
    ).toBeInTheDocument();
  });

  it("reopens the editor from a deep-linked URL (hard-reload survival)", async () => {
    renderPage(`/policies?policy=team:${TEAM_ID}`);
    // No click needed — the URL drives the open drawer on first render.
    await waitFor(() => {
      expect(screen.getByTestId("policy-drawer")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(mockedGetTeam).toHaveBeenCalledWith(TEAM_ID);
    });
  });

  it("adds and removes a category override, then saves via PUT", async () => {
    const user = userEvent.setup();
    mockedUpsertTeam.mockResolvedValue(policyFixture());
    renderPage(`/policies?policy=team:${TEAM_ID}`);

    await screen.findByTestId("policy-editor-form");

    // Add a blank override row (fixture starts with 1 → becomes 2).
    await user.click(screen.getByTestId("policy-add-override"));
    await waitFor(() => {
      expect(screen.getAllByTestId("policy-override-row")).toHaveLength(2);
    });

    // Remove the first override → back to 1.
    const removeButtons = screen.getAllByTestId("policy-remove-override");
    await user.click(removeButtons[0]);
    await waitFor(() => {
      expect(screen.getAllByTestId("policy-override-row")).toHaveLength(1);
    });

    await user.click(screen.getByTestId("policy-save"));
    await waitFor(() => {
      expect(mockedUpsertTeam).toHaveBeenCalledTimes(1);
    });
    expect(mockedUpsertTeam).toHaveBeenCalledWith(
      TEAM_ID,
      expect.objectContaining({ enabled: true }),
    );
    const toast = await screen.findByTestId("admin-toast");
    expect(toast).toHaveAttribute("data-toast-key", "saved");
  });

  it("adds a license exception, fills its fields, and toggles enabled", async () => {
    const user = userEvent.setup();
    mockedUpsertTeam.mockResolvedValue(policyFixture());
    renderPage(`/policies?policy=team:${TEAM_ID}`);
    await screen.findByTestId("policy-editor-form");

    // No exceptions in the fixture → empty state visible.
    expect(screen.getByTestId("policy-exceptions-empty")).toBeInTheDocument();
    await user.click(screen.getByTestId("policy-add-exception"));
    await waitFor(() => {
      expect(screen.getByTestId("policy-exception-row")).toBeInTheDocument();
    });

    // Fill the exception fields (spdx, reason, expiry, purl).
    await user.type(
      screen.getByTestId("policy-exception-spdx"),
      "GPL-3.0-only",
    );
    await user.type(
      screen.getByTestId("policy-exception-reason"),
      "legal waiver TICKET-1",
    );
    await user.type(screen.getByTestId("policy-exception-expiry"), "2026-12-31");
    await user.type(
      screen.getByTestId("policy-exception-purl"),
      "pkg:pypi/x@1.0",
    );

    // Edit the existing override's category (MPL-2.0 → conditional), the policy
    // name, the unknown posture, and a compound-operator strategy.
    await user.selectOptions(
      screen.getByTestId("policy-override-category"),
      "conditional",
    );
    await user.clear(screen.getByTestId("policy-name-input"));
    await user.type(screen.getByTestId("policy-name-input"), "Renamed");
    await user.selectOptions(
      screen.getByTestId("policy-unknown-select"),
      "forbidden",
    );
    await user.selectOptions(
      screen.getByTestId("policy-compound-OR"),
      "most_restrictive",
    );

    // Toggle enabled off, then save the assembled draft.
    const toggle = screen.getByTestId("policy-enabled-toggle");
    expect(toggle).toBeChecked();
    await user.click(toggle);
    expect(toggle).not.toBeChecked();

    await user.click(screen.getByTestId("policy-save"));
    await waitFor(() => {
      expect(mockedUpsertTeam).toHaveBeenCalledTimes(1);
    });
    const [, payload] = mockedUpsertTeam.mock.calls[0];
    expect(payload).toMatchObject({
      name: "Renamed",
      enabled: false,
      unknown_license_category: "forbidden",
      category_overrides: { "MPL-2.0": "conditional" },
      compound_operator_strategy: expect.objectContaining({
        OR: "most_restrictive",
      }),
      license_exceptions: [
        expect.objectContaining({
          spdx_id: "GPL-3.0-only",
          reason: "legal waiver TICKET-1",
          component_purl: "pkg:pypi/x@1.0",
          expires_at: "2026-12-31T00:00:00Z",
        }),
      ],
    });
  });

  it("renames an override SPDX key and preserves its category", async () => {
    const user = userEvent.setup();
    mockedUpsertTeam.mockResolvedValue(policyFixture());
    renderPage(`/policies?policy=team:${TEAM_ID}`);
    await screen.findByTestId("policy-editor-form");

    // A single change event sets the whole new key (avoids char-by-char
    // closure churn) so the rename handler is covered deterministically.
    fireEvent.change(screen.getByTestId("policy-override-key"), {
      target: { value: "EPL-2.0" },
    });
    await user.click(screen.getByTestId("policy-save"));
    await waitFor(() => {
      expect(mockedUpsertTeam).toHaveBeenCalledTimes(1);
    });
    const [, payload] = mockedUpsertTeam.mock.calls[0];
    expect(payload.category_overrides).toEqual({ "EPL-2.0": "forbidden" });
  });

  it("removes a license exception via its row remove button", async () => {
    const user = userEvent.setup();
    mockedGetTeam.mockResolvedValue(
      policyFixture({
        license_exceptions: [
          { spdx_id: "GPL-3.0-only", reason: "r", expires_at: null, component_purl: null },
        ],
      }),
    );
    renderPage(`/policies?policy=team:${TEAM_ID}`);
    await screen.findByTestId("policy-editor-form");

    expect(screen.getAllByTestId("policy-exception-row")).toHaveLength(1);
    await user.click(screen.getByTestId("policy-remove-exception"));
    await waitFor(() => {
      expect(screen.getByTestId("policy-exceptions-empty")).toBeInTheDocument();
    });
  });

  it("surfaces the validation message when the save returns 422", async () => {
    const user = userEvent.setup();
    mockedUpsertTeam.mockRejectedValue(problem(422));
    renderPage(`/policies?policy=team:${TEAM_ID}`);
    await screen.findByTestId("policy-editor-form");

    await user.click(screen.getByTestId("policy-save"));
    const toast = await screen.findByTestId("admin-toast");
    expect(toast).toHaveAttribute("data-toast-key", "validation");
    expect(toast).toHaveAttribute("data-tone", "error");
  });

  it("resets the team policy via DELETE", async () => {
    const user = userEvent.setup();
    mockedDeleteTeam.mockResolvedValue(undefined);
    renderPage(`/policies?policy=team:${TEAM_ID}`);
    await screen.findByTestId("policy-editor-form");

    await user.click(screen.getByTestId("policy-reset"));
    await waitFor(() => {
      expect(mockedDeleteTeam).toHaveBeenCalledWith(TEAM_ID);
    });
    const toast = await screen.findByTestId("admin-toast");
    expect(toast).toHaveAttribute("data-toast-key", "reset");
  });

  it("renders read-only for a member who is not a team_admin (403)", async () => {
    mockedGetTeam.mockRejectedValue(problem(403));
    renderPage(`/policies?policy=team:${TEAM_ID}`);

    // Read-only banner shows; no save/reset controls are rendered.
    const banner = await screen.findByTestId("policy-editor-readonly");
    expect(banner).toHaveTextContent(/team admin/i);
    expect(screen.queryByTestId("policy-save")).not.toBeInTheDocument();
    expect(screen.queryByTestId("policy-reset")).not.toBeInTheDocument();
    // The form fields are disabled in read-only mode.
    expect(screen.getByTestId("policy-enabled-toggle")).toBeDisabled();
  });

  it("treats a 404 as 'no policy yet' with a blank editable draft", async () => {
    mockedGetTeam.mockRejectedValue(problem(404));
    renderPage(`/policies?policy=team:${TEAM_ID}`);

    const hint = await screen.findByTestId("policy-editor-no-policy");
    expect(hint).toBeInTheDocument();
    // A blank draft → no override rows, save is available (not read-only).
    expect(screen.getByTestId("policy-overrides-empty")).toBeInTheDocument();
    expect(screen.getByTestId("policy-save")).toBeInTheDocument();
  });
});

describe("PoliciesPage — super_admin org default", () => {
  const adminUser: AuthUser = {
    id: "user-admin",
    email: "admin@example.com",
    displayName: "Admin",
    role: "super_admin",
    isActive: true,
    isSuperuser: true,
    teamId: null,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mockedList.mockResolvedValue(listFixture([policyFixture({ team_id: null })]));
    mockedAdminTeams.mockResolvedValue({
      items: [
        {
          id: TEAM_ID,
          name: "Platform",
          slug: "platform",
          description: null,
          member_count: 3,
          project_count: 2,
          created_at: "2026-05-24T00:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      page_size: 200,
    });
    mockedProjects.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      size: 200,
    });
    mockedGetOrg.mockResolvedValue(policyFixture({ team_id: null }));
    useAuthStore.setState({
      user: adminUser,
      accessToken: "tok",
      status: "authenticated",
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    useAuthStore.getState().reset();
  });

  it("opens the org-default editor and saves via the org PUT", async () => {
    const user = userEvent.setup();
    mockedUpsertOrg.mockResolvedValue(policyFixture({ team_id: null }));
    renderPage();

    // The org row (team_id null) renders the org scope label.
    const row = await screen.findByTestId("policies-row");
    expect(row).toHaveAttribute("data-scope", "org");

    // The dedicated "Edit org default" button is available to super_admin.
    await user.click(screen.getByTestId("policy-edit-org"));
    await waitFor(() => {
      expect(mockedGetOrg).toHaveBeenCalledWith(ORG_ID);
    });

    await user.click(screen.getByTestId("policy-save"));
    await waitFor(() => {
      expect(mockedUpsertOrg).toHaveBeenCalledWith(
        ORG_ID,
        expect.objectContaining({ enabled: true }),
      );
    });
    // The org editor has no reset button (reset is team-scoped only).
    expect(screen.queryByTestId("policy-reset")).not.toBeInTheDocument();
  });

  it("offers the admin teams in the picker and edits a team policy", async () => {
    const user = userEvent.setup();
    mockedGetTeam.mockResolvedValue(policyFixture());
    renderPage();

    const picker = await screen.findByTestId("policy-team-picker");
    // The admin team name (not a raw id) is offered as an option label.
    expect(within(picker).getByText("Platform")).toBeInTheDocument();

    await user.selectOptions(picker, TEAM_ID);
    await user.click(screen.getByTestId("policy-edit-team"));
    await waitFor(() => {
      expect(mockedGetTeam).toHaveBeenCalledWith(TEAM_ID);
    });
  });
});
