/**
 * The assignee picker - ER28b.
 *
 * The guide says a finding you own can be "handed to another team member".
 * That sentence was written before the screen could do it, and nothing caught
 * the gap for weeks: rule 4 asks for an assertion per promised behaviour, and
 * this file is that assertion for this promise.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { VulnerabilityAssignmentEditor } from "@/features/projects/components/VulnerabilityAssignmentEditor";
import type { VulnerabilityDetail } from "@/features/projects/api/vulnerabilitiesApi";
import { useAuthStore } from "@/stores/authStore";

vi.mock("@/features/projects/api/vulnerabilitiesApi", async () => {
  const actual = await vi.importActual<
    typeof import("@/features/projects/api/vulnerabilitiesApi")
  >("@/features/projects/api/vulnerabilitiesApi");
  return {
    ...actual,
    updateFindingAssignment: vi.fn(),
    fetchAssignableMembers: vi.fn(),
  };
});

import {
  fetchAssignableMembers,
  updateFindingAssignment,
} from "@/features/projects/api/vulnerabilitiesApi";

const patch = vi.mocked(updateFindingAssignment);
const list = vi.mocked(fetchAssignableMembers);

const ME = "11111111-1111-1111-1111-111111111111";
const NAMED = "22222222-2222-2222-2222-222222222222";
const UNNAMED = "33333333-3333-3333-3333-333333333333";
const OTHER_UNNAMED = "44444444-4444-4444-4444-444444444444";
const FINDING = "55555555-5555-5555-5555-555555555555";
const PROJECT = "66666666-6666-6666-6666-666666666666";

function detail(over: Partial<VulnerabilityDetail> = {}): VulnerabilityDetail {
  return {
    id: FINDING,
    project_id: PROJECT,
    assignee_user_id: null,
    assignee_is_active: null,
    due_on: null,
    sla_due_date: null,
    effective_due_date: null,
    due_source: null,
    manual_due_ignored: false,
    ticket_url: null,
    ticket_key: null,
    ...over,
  } as never;
}

function wrap({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const render_ = (d: VulnerabilityDetail) =>
  render(<VulnerabilityAssignmentEditor detail={d} projectId={PROJECT} />, {
    wrapper: wrap,
  });

describe("assignee picker", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.setState({ user: { id: ME } as never });
    patch.mockResolvedValue(detail({ assignee_user_id: NAMED, assignee_is_active: true }));
    list.mockResolvedValue({
      members: [
        { user_id: NAMED, full_name: "Dana Okafor" },
        { user_id: UNNAMED, full_name: null },
        { user_id: OTHER_UNNAMED, full_name: null },
      ],
      total: 3,
    });
  });

  it("hands a finding you own to another team member", async () => {
    // The guide's promise, walked end to end.
    render_(detail({ assignee_user_id: ME, assignee_is_active: true }));

    const select = await screen.findByTestId("vulnerability-assignment-member-select");
    await waitFor(() =>
      expect(screen.getByRole("option", { name: "Dana Okafor" })).toBeInTheDocument(),
    );
    fireEvent.change(select, { target: { value: NAMED } });

    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith(FINDING, { assignee_user_id: NAMED }),
    );
  });

  it("shows the id for somebody with no name, and keeps two of them apart", async () => {
    // Not "does not crash": the id has to be what is rendered, and two
    // unnamed people must not collapse into one indistinguishable label.
    render_(detail({ assignee_user_id: ME, assignee_is_active: true }));

    const first = await screen.findByRole("option", { name: UNNAMED });
    const second = await screen.findByRole("option", { name: OTHER_UNNAMED });
    expect(first).toHaveValue(UNNAMED);
    expect(second).toHaveValue(OTHER_UNNAMED);
    expect(first.textContent).not.toBe(second.textContent);
  });

  it("offers nothing while an active person owns it", async () => {
    render_(detail({ assignee_user_id: NAMED, assignee_is_active: true }));
    await waitFor(() =>
      expect(screen.getByTestId("vulnerability-assignment-owned-elsewhere")).toBeInTheDocument(),
    );
    expect(
      screen.queryByTestId("vulnerability-assignment-member-select"),
    ).not.toBeInTheDocument();
    // And it does not ask the server for a list it will not show.
    expect(list).not.toHaveBeenCalled();
  });

  it("offers the picker for a finding whose owner cannot act", async () => {
    render_(detail({ assignee_user_id: NAMED, assignee_is_active: false }));
    expect(
      await screen.findByTestId("vulnerability-assignment-member-select"),
    ).toBeInTheDocument();
  });
});
