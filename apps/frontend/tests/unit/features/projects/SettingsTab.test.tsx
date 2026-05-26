/**
 * SettingsTab — unit tests (Step 4-B).
 *
 * Coverage targets:
 *   - Form pre-fills the project's current values.
 *   - Empty-name validation surfaces an inline error before any API call.
 *   - Successful PATCH triggers `updateProject` and shows the saved toast.
 *   - Archive flow uses an inline confirm strip and then calls
 *     `archiveProject` (the soft-delete endpoint).
 *   - When the project is already archived, the "Unarchive" button is shown
 *     and clicking it dispatches `unarchiveProject`.
 *   - PATCH error is surfaced as an inline alert.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsTab } from "@/features/projects/components/SettingsTab";
import { ProblemError } from "@/lib/problem";
import type { ProjectPublic } from "@/lib/projectsApi";

vi.mock("@/lib/projectsApi", async () => {
  const actual = await vi.importActual<
    typeof import("@/lib/projectsApi")
  >("@/lib/projectsApi");
  return {
    ...actual,
    updateProject: vi.fn(),
    archiveProject: vi.fn(),
    unarchiveProject: vi.fn(),
  };
});

import {
  archiveProject,
  unarchiveProject,
  updateProject,
} from "@/lib/projectsApi";

const mockedUpdate = vi.mocked(updateProject);
const mockedArchive = vi.mocked(archiveProject);
const mockedUnarchive = vi.mocked(unarchiveProject);

function project(overrides: Partial<ProjectPublic> = {}): ProjectPublic {
  return {
    id: "proj-1",
    team_id: "team-1",
    name: "Demo",
    slug: "demo",
    description: "An example",
    git_url: "https://github.com/example/demo",
    default_branch: "main",
    visibility: "team",
    archived_at: null,
    created_by_user_id: null,
    latest_scan_id: null,
    latest_scan_status: null,
    severity_summary: null,
    has_git_credential: false,
    scan_count: 0,
    release_count: 0,
    last_scan_at: null,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
    ...overrides,
  };
}

function renderTab(p: ProjectPublic | null = project()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <SettingsTab projectId="proj-1" project={p} />
    </QueryClientProvider>,
  );
}

describe("SettingsTab", () => {
  beforeEach(() => {
    mockedUpdate.mockReset();
    mockedArchive.mockReset();
    mockedUnarchive.mockReset();
  });

  it("pre-fills the form with the project's current values", () => {
    renderTab();
    const name = screen.getByTestId("settings-name-input") as HTMLInputElement;
    const desc = screen.getByTestId(
      "settings-description-input",
    ) as HTMLTextAreaElement;
    const url = screen.getByTestId("settings-git-url-input") as HTMLInputElement;
    const branch = screen.getByTestId(
      "settings-default-branch-input",
    ) as HTMLInputElement;
    expect(name.value).toBe("Demo");
    expect(desc.value).toBe("An example");
    expect(url.value).toBe("https://github.com/example/demo");
    expect(branch.value).toBe("main");
  });

  it("shows an inline name-required error when the name is cleared on submit", async () => {
    const user = userEvent.setup();
    renderTab();
    await user.clear(screen.getByTestId("settings-name-input"));
    await user.click(screen.getByTestId("settings-save-button"));
    await waitFor(() => {
      expect(screen.getByTestId("settings-name-error")).toBeInTheDocument();
    });
    expect(mockedUpdate).not.toHaveBeenCalled();
  });

  it("submits the form and shows the saved toast on success", async () => {
    mockedUpdate.mockResolvedValueOnce(project({ name: "Renamed" }));
    const user = userEvent.setup();
    renderTab();
    const name = screen.getByTestId("settings-name-input");
    await user.clear(name);
    await user.type(name, "Renamed");
    await user.click(screen.getByTestId("settings-save-button"));
    await waitFor(() => {
      expect(mockedUpdate).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ name: "Renamed" }),
      );
    });
    await waitFor(() => {
      expect(screen.getByTestId("settings-toast")).toBeInTheDocument();
    });
  });

  it("archive flow shows the confirm strip then calls archiveProject", async () => {
    mockedArchive.mockResolvedValueOnce(undefined);
    const user = userEvent.setup();
    renderTab();
    await user.click(screen.getByTestId("settings-archive-button"));
    await waitFor(() => {
      expect(
        screen.getByTestId("settings-archive-confirm"),
      ).toBeInTheDocument();
    });
    await user.click(screen.getByTestId("settings-archive-confirm-ok"));
    await waitFor(() => {
      expect(mockedArchive).toHaveBeenCalledWith("proj-1");
    });
  });

  it("renders the Unarchive button when the project is already archived", async () => {
    mockedUnarchive.mockResolvedValueOnce(
      project({ archived_at: null }),
    );
    const user = userEvent.setup();
    renderTab(project({ archived_at: "2026-05-01T00:00:00Z" }));
    expect(
      screen.getByTestId("settings-unarchive-button"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("settings-archive-button"),
    ).not.toBeInTheDocument();
    await user.click(screen.getByTestId("settings-unarchive-button"));
    await waitFor(() => {
      expect(mockedUnarchive).toHaveBeenCalledWith("proj-1");
    });
  });

  it("shows an inline error when updateProject returns a ProblemError", async () => {
    mockedUpdate.mockRejectedValueOnce(
      new ProblemError("Conflict", {
        status: 409,
        title: "Conflict",
        detail: "A project with this name already exists.",
        problem: null,
      }),
    );
    const user = userEvent.setup();
    renderTab();
    const name = screen.getByTestId("settings-name-input");
    await user.clear(name);
    await user.type(name, "Other");
    await user.click(screen.getByTestId("settings-save-button"));
    await waitFor(() => {
      expect(screen.getByTestId("settings-save-error")).toHaveTextContent(
        "A project with this name already exists.",
      );
    });
  });

  it("validates an invalid git URL inline before calling the API", async () => {
    const user = userEvent.setup();
    renderTab();
    const url = screen.getByTestId("settings-git-url-input");
    await user.clear(url);
    await user.type(url, "not-a-url");
    await user.click(screen.getByTestId("settings-save-button"));
    await waitFor(() => {
      expect(screen.getByTestId("settings-git-url-error")).toBeInTheDocument();
    });
    expect(mockedUpdate).not.toHaveBeenCalled();
  });

  it("renders gracefully when project is null (initial load)", () => {
    renderTab(null);
    expect(screen.getByTestId("settings-tab")).toBeInTheDocument();
    expect(screen.getByTestId("settings-form")).toBeInTheDocument();
  });

  // ---- feature #18 — git credential (private repository) ----------------

  it("shows the credential input when no credential is configured", () => {
    renderTab(project({ has_git_credential: false }));
    expect(
      screen.getByTestId("project-git-credential-input"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("project-git-credential-configured"),
    ).not.toBeInTheDocument();
    // Save is disabled until a token is entered.
    expect(screen.getByTestId("project-git-credential-save")).toBeDisabled();
  });

  it("uses a password-type input that never echoes a value", () => {
    renderTab(project({ has_git_credential: false }));
    const input = screen.getByTestId(
      "project-git-credential-input",
    ) as HTMLInputElement;
    expect(input.type).toBe("password");
  });

  it("saves a credential via PATCH { git_credential } (trimmed)", async () => {
    mockedUpdate.mockResolvedValueOnce(project({ has_git_credential: true }));
    const user = userEvent.setup();
    renderTab(project({ has_git_credential: false }));
    await user.type(
      screen.getByTestId("project-git-credential-input"),
      "  ghp_secret_token  ",
    );
    await user.click(screen.getByTestId("project-git-credential-save"));
    await waitFor(() => {
      expect(mockedUpdate).toHaveBeenCalledWith("proj-1", {
        git_credential: "ghp_secret_token",
      });
    });
  });

  it("renders the configured state with a masked badge when a credential is set", () => {
    renderTab(project({ has_git_credential: true }));
    expect(
      screen.getByTestId("project-git-credential-configured"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("project-git-credential-badge"),
    ).toBeInTheDocument();
    // The input is hidden behind the Replace affordance in the configured state.
    expect(
      screen.queryByTestId("project-git-credential-input"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByTestId("project-git-credential-remove"),
    ).toBeInTheDocument();
  });

  it("clears a credential via PATCH { clear_git_credential: true }", async () => {
    mockedUpdate.mockResolvedValueOnce(project({ has_git_credential: false }));
    const user = userEvent.setup();
    renderTab(project({ has_git_credential: true }));
    await user.click(screen.getByTestId("project-git-credential-remove"));
    await waitFor(() => {
      expect(mockedUpdate).toHaveBeenCalledWith("proj-1", {
        clear_git_credential: true,
      });
    });
  });

  it("reveals the input on Replace and never sends both fields", async () => {
    const user = userEvent.setup();
    renderTab(project({ has_git_credential: true }));
    await user.click(screen.getByTestId("project-git-credential-replace"));
    const input = screen.getByTestId("project-git-credential-input");
    expect(input).toBeInTheDocument();
    mockedUpdate.mockResolvedValueOnce(project({ has_git_credential: true }));
    await user.type(input, "new_token");
    await user.click(screen.getByTestId("project-git-credential-save"));
    await waitFor(() => {
      expect(mockedUpdate).toHaveBeenCalledWith("proj-1", {
        git_credential: "new_token",
      });
    });
    // Mutual-exclusion guard: the payload must NOT carry clear_git_credential.
    const payload = mockedUpdate.mock.calls.at(-1)?.[1];
    expect(payload).not.toHaveProperty("clear_git_credential");
  });

  it("surfaces a 503 RFC7807 credential-encryption error inline", async () => {
    mockedUpdate.mockRejectedValueOnce(
      new ProblemError("Credential Encryption Unavailable", {
        status: 503,
        title: "Credential Encryption Unavailable",
        detail: "Credential encryption is not configured on this server.",
        problem: null,
      }),
    );
    const user = userEvent.setup();
    renderTab(project({ has_git_credential: false }));
    await user.type(
      screen.getByTestId("project-git-credential-input"),
      "ghp_token",
    );
    await user.click(screen.getByTestId("project-git-credential-save"));
    await waitFor(() => {
      expect(
        screen.getByTestId("project-git-credential-error"),
      ).toHaveTextContent("Credential encryption is not configured");
    });
  });
});
