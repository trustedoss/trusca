/**
 * The note exists to answer one question: is this our answer or the
 * organization's?
 *
 * The silent cases matter as much as the visible one. A note that also
 * appeared for a team's own decision would say nothing the screen does not
 * already show, and it sits in a drawer where every line competes with the
 * component's own detail.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { InheritedVerdictNote } from "@/features/projects/components/InheritedVerdictNote";

const getEffectiveVerdict = vi.fn();

vi.mock("@/lib/organizationVerdictsApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/organizationVerdictsApi")>()),
  getEffectiveVerdict: (...args: unknown[]) => getEffectiveVerdict(...args),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

function renderNote() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <InheritedVerdictNote projectId="proj-1" componentId="pkg-1" />
    </QueryClientProvider>,
  );
}

describe("InheritedVerdictNote", () => {
  beforeEach(() => {
    getEffectiveVerdict.mockReset();
  });

  it("shows the organization's answer and its reason when inherited", async () => {
    getEffectiveVerdict.mockResolvedValue({
      project_id: "proj-1",
      component_id: "pkg-1",
      status: "approved",
      scope: "organization",
      justification: "reviewed centrally for the whole organization",
    });

    renderNote();

    await waitFor(() =>
      expect(screen.getByTestId("component-inherited-verdict")).toBeInTheDocument(),
    );
    expect(
      screen.getByTestId("component-inherited-verdict-reason"),
    ).toHaveTextContent("reviewed centrally for the whole organization");
  });

  it("says nothing when the team decided for itself", async () => {
    // The case that would make the note misleading rather than merely noisy:
    // labelling a local decision as the organization's sends somebody to an
    // administrator to change something their own team controls.
    getEffectiveVerdict.mockResolvedValue({
      project_id: "proj-1",
      component_id: "pkg-1",
      status: "rejected",
      scope: "project",
      justification: null,
    });

    renderNote();

    await waitFor(() => expect(getEffectiveVerdict).toHaveBeenCalled());
    expect(
      screen.queryByTestId("component-inherited-verdict"),
    ).not.toBeInTheDocument();
  });

  it("says nothing when nobody has decided", async () => {
    getEffectiveVerdict.mockResolvedValue({
      project_id: "proj-1",
      component_id: "pkg-1",
      status: null,
      scope: "none",
      justification: null,
    });

    renderNote();

    await waitFor(() => expect(getEffectiveVerdict).toHaveBeenCalled());
    expect(
      screen.queryByTestId("component-inherited-verdict"),
    ).not.toBeInTheDocument();
  });
});
