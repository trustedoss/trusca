/**
 * Route-level boundary and Suspense inside the shell.
 *
 * Before this, the only boundary sat above the whole app and the only
 * Suspense sat above AppShell, so a crashed page replaced the navigation and
 * every first visit to a lazy route unmounted the sidebar while its chunk
 * loaded. These tests pin that both now stop at the content area.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, within } from "@testing-library/react";
import { lazy } from "react";
import {
  MemoryRouter,
  Route,
  Routes,
  useNavigate,
  type NavigateFunction,
} from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/AppShell";

vi.mock("@/features/about/api/useDeploymentFeatures", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/features/about/api/useDeploymentFeatures")>();
  return { ...actual, useDeploymentFeatures: () => ({}) };
});

vi.mock("@/features/notifications/useNotifications", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/features/notifications/useNotifications")>();
  return { ...actual, useUnreadCount: () => ({ data: undefined }) };
});

vi.mock("@/hooks/useNavBadges", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/useNavBadges")>();
  return { ...actual, useNavBadges: () => ({}) };
});

vi.mock("@/lib/clientErrorApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/clientErrorApi")>();
  return { ...actual, reportClientError: vi.fn() };
});

function Broken(): JSX.Element {
  throw new Error("page exploded");
}

// A lazy route whose chunk never arrives, so the fallback stays visible.
const NeverLoads = lazy(() => new Promise<{ default: () => JSX.Element }>(() => {}));

let navigate: NavigateFunction = () => {};
function CaptureNavigate() {
  navigate = useNavigate();
  return null;
}

function renderAt(path: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <CaptureNavigate />
        <Routes>
          <Route path="/" element={<AppShell />}>
            <Route path="broken" element={<Broken />} />
            <Route path="slow" element={<NeverLoads />} />
            <Route path="ok" element={<div data-testid="ok-page">fine</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AppShell route boundary", () => {
  let consoleSpy: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => {
    consoleSpy.mockRestore();
  });

  it("keeps the shell when a page crashes and recovers on navigation", async () => {
    renderAt("/broken");

    const main = screen.getByTestId("app-main");
    expect(within(main).getByTestId("error-boundary-fallback")).toBeInTheDocument();
    expect(within(main).queryByText(/page exploded/)).not.toBeNull();
    // Navigation is still there to leave the broken page.
    expect(screen.getAllByTestId("nav-scans").length).toBeGreaterThan(0);

    await act(async () => {
      navigate("/ok");
    });

    expect(screen.getByTestId("ok-page")).toBeInTheDocument();
    expect(screen.queryByTestId("error-boundary-fallback")).toBeNull();
  });

  it("shows the skeleton inside the content area while a route chunk loads", () => {
    renderAt("/slow");

    const main = screen.getByTestId("app-main");
    expect(within(main).getByTestId("route-loading-fallback")).toBeInTheDocument();
    expect(screen.getAllByTestId("nav-scans").length).toBeGreaterThan(0);
  });
});
