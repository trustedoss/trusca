/**
 * ProjectCreatePage — unit tests.
 *
 * Covers form rendering, zod validation (required name, git URL format),
 * successful submission navigation, and API error display.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ProjectCreatePage } from "@/features/projects/ProjectCreatePage";
import { ProblemError } from "@/lib/problem";
import { useAuthStore } from "@/stores/authStore";

vi.mock("@/lib/projectsApi", () => ({
  createProject: vi.fn(),
  listProjects: vi.fn(),
}));

// useNavigate is wired through MemoryRouter — we spy on it via the mock so we
// can assert the target path without mounting the full App routing tree.
const mockNavigate = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

import { createProject } from "@/lib/projectsApi";
const mockedCreateProject = vi.mocked(createProject);

const fakeUser = {
  id: "u1",
  email: "e@e.com",
  displayName: "E",
  role: "developer" as const,
  isActive: true,
  isSuperuser: false,
  teamId: "team-1",
  teams: [],
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ProjectCreatePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ProjectCreatePage", () => {
  beforeEach(() => {
    mockedCreateProject.mockReset();
    mockNavigate.mockReset();
    useAuthStore.setState({
      user: fakeUser,
      accessToken: "tok-1",
      status: "authenticated",
      isAuthenticated: true,
    });
  });

  it("renders name, description, and git URL fields", () => {
    renderPage();
    expect(screen.getByTestId("project-create-form")).toBeInTheDocument();
    expect(screen.getByTestId("project-name-input")).toBeInTheDocument();
    expect(screen.getByTestId("project-description-input")).toBeInTheDocument();
    expect(screen.getByTestId("project-git-url-input")).toBeInTheDocument();
  });

  it("shows a validation error when the name field is empty on submit", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByTestId("project-create-submit"));
    await waitFor(() => {
      expect(screen.getByTestId("project-name-input")).toHaveAttribute(
        "aria-invalid",
        "true",
      );
    });
    // The name error paragraph should be present (use testid to avoid matching the "Name" label)
    expect(
      screen.getByTestId("project-name-error"),
    ).toBeInTheDocument();
  });

  it("shows a validation error when the git URL is not a valid http(s) URL", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.type(screen.getByTestId("project-name-input"), "My Project");
    await user.type(
      screen.getByTestId("project-git-url-input"),
      "not-a-url",
    );
    await user.click(screen.getByTestId("project-create-submit"));
    await waitFor(() => {
      expect(screen.getByTestId("project-git-url-input")).toHaveAttribute(
        "aria-invalid",
        "true",
      );
    });
  });

  it("navigates to the new project after successful submission", async () => {
    const user = userEvent.setup();
    mockedCreateProject.mockResolvedValueOnce({
      id: "proj-123",
      team_id: "team-1",
      name: "My Project",
      slug: "my-project",
      description: null,
      git_url: null,
      default_branch: null,
      visibility: "team",
      archived_at: null,
      created_by_user_id: "u1",
      latest_scan_id: null,
      latest_scan_status: null,
      severity_summary: null,
      has_git_credential: false,
      scan_count: 0,
      release_count: 0,
      last_scan_at: null,
      created_at: "2026-05-08T00:00:00Z",
      updated_at: "2026-05-08T00:00:00Z",
    });
    renderPage();
    await user.type(screen.getByTestId("project-name-input"), "My Project");
    await user.click(screen.getByTestId("project-create-submit"));
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith("/projects/proj-123");
    });
  });

  it("shows the inline error alert when the API returns a ProblemError", async () => {
    const user = userEvent.setup();
    mockedCreateProject.mockRejectedValueOnce(
      new ProblemError("Conflict", {
        status: 409,
        title: "Conflict",
        detail: "A project with this name already exists.",
        problem: null,
      }),
    );
    renderPage();
    await user.type(screen.getByTestId("project-name-input"), "Duplicate");
    await user.click(screen.getByTestId("project-create-submit"));
    await waitFor(() => {
      expect(screen.getByTestId("project-create-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("project-create-error")).toHaveTextContent(
      "A project with this name already exists.",
    );
  });
});
