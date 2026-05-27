/**
 * ReleaseSwitcher — unit tests (feature #28 Phase 1, persistent version context).
 *
 * Validates the always-visible header dropdown:
 *   - the trigger renders the LATEST context label when not historical,
 *   - the trigger renders the HISTORICAL (read-only) label when an older scan
 *     is pinned (i.e. it reflects `?scan=` on mount),
 *   - the menu lists a "Latest" item plus every release, newest-first,
 *   - selecting a release item invokes onSelectRelease with that scan id,
 *   - selecting "Latest" invokes onSelectLatest,
 *   - the empty state disables the trigger when there are no releases.
 *
 * We mock the wire layer (mirrors ReleasesTab.test.tsx / ProjectDetailPage
 * mocking style) so the component renders without a backend. Radix's
 * DropdownMenu needs a few DOM APIs jsdom omits — polyfilled below.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ReleaseListResponse,
  ReleaseSnapshot,
} from "@/features/projects/api/releasesApi";
import { ReleaseSwitcher } from "@/features/projects/components/ReleaseSwitcher";

vi.mock("@/features/projects/api/releasesApi", async () => {
  return {
    listProjectReleases: vi.fn(),
  };
});

import { listProjectReleases } from "@/features/projects/api/releasesApi";

const mockedList = vi.mocked(listProjectReleases);

beforeAll(() => {
  // Radix DropdownMenu uses these DOM APIs that jsdom does not implement.
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false;
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {};
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {};
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {};
  }
});

function snapshot(
  scanId: string,
  overrides: Partial<ReleaseSnapshot> = {},
): ReleaseSnapshot {
  return {
    scan_id: scanId,
    release: null,
    created_at: "2026-05-22T10:00:00Z",
    risk_score: 80,
    severity_summary: { critical: 10, high: 0, medium: 0, low: 0 },
    gate_status: "fail",
    component_count: 42,
    ...overrides,
  };
}

function listResponse(
  items: ReleaseSnapshot[],
  total = items.length,
): ReleaseListResponse {
  return { items, total, page: 1, size: 50 };
}

interface RenderOptions {
  pinnedScanId?: string;
  latestScanId?: string | null;
  isHistorical?: boolean;
}

function renderSwitcher(opts: RenderOptions = {}) {
  const onSelectRelease = vi.fn();
  const onSelectLatest = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const utils = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ReleaseSwitcher
          projectId="proj-1"
          pinnedScanId={opts.pinnedScanId}
          latestScanId={opts.latestScanId ?? "scan-latest"}
          isHistorical={opts.isHistorical ?? false}
          onSelectRelease={onSelectRelease}
          onSelectLatest={onSelectLatest}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, onSelectRelease, onSelectLatest };
}

const TWO_RELEASES = listResponse([
  snapshot("scan-latest", { release: "v0.2", gate_status: "fail" }),
  snapshot("scan-old", {
    release: "v0.1",
    gate_status: "pass",
    severity_summary: { critical: 0, high: 0, medium: 0, low: 0 },
  }),
]);

describe("ReleaseSwitcher", () => {
  beforeEach(() => {
    mockedList.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("trigger shows the LATEST context label when not historical", async () => {
    mockedList.mockResolvedValue(TWO_RELEASES);
    renderSwitcher({ latestScanId: "scan-latest", isHistorical: false });

    await waitFor(() => {
      expect(
        screen.getByTestId("release-switcher-label").textContent,
      ).toContain("v0.2");
    });
    const label = screen.getByTestId("release-switcher-label").textContent ?? "";
    // "Release: v0.2 · Latest" — the live-view affordance, not read-only.
    expect(label).toContain("Latest");
    expect(label).not.toContain("read-only");
    expect(screen.getByTestId("release-switcher")).toHaveAttribute(
      "data-historical",
      "false",
    );
  });

  it("trigger reflects the pinned ?scan= snapshot on mount (read-only)", async () => {
    mockedList.mockResolvedValue(TWO_RELEASES);
    // The page resolved ?scan=scan-old → an older snapshot, so historical.
    renderSwitcher({
      pinnedScanId: "scan-old",
      latestScanId: "scan-latest",
      isHistorical: true,
    });

    await waitFor(() => {
      expect(
        screen.getByTestId("release-switcher-label").textContent,
      ).toContain("v0.1");
    });
    const label = screen.getByTestId("release-switcher-label").textContent ?? "";
    expect(label).toContain("read-only");
    expect(screen.getByTestId("release-switcher")).toHaveAttribute(
      "data-pinned-scan-id",
      "scan-old",
    );
    expect(screen.getByTestId("release-switcher")).toHaveAttribute(
      "data-historical",
      "true",
    );
  });

  it("menu lists a 'Latest' item plus every release, newest-first", async () => {
    mockedList.mockResolvedValue(TWO_RELEASES);
    renderSwitcher();

    await waitFor(() => {
      expect(screen.getByTestId("release-switcher")).toBeEnabled();
    });
    await userEvent.click(screen.getByTestId("release-switcher"));

    await waitFor(() => {
      expect(screen.getByTestId("release-switcher-latest")).toBeInTheDocument();
    });
    const items = screen.getAllByTestId("release-switcher-item");
    expect(items).toHaveLength(2);
    // Newest-first ordering preserved from the wire response.
    expect(items[0]).toHaveAttribute("data-scan-id", "scan-latest");
    expect(items[1]).toHaveAttribute("data-scan-id", "scan-old");
    // The latest row carries the "Latest" tag badge (not color-only signal).
    expect(items[0].textContent).toContain("v0.2");
    expect(
      items[0].querySelector('[data-testid="release-switcher-item-latest"]'),
    ).not.toBeNull();
    // A failing gate surfaces a labelled Fail signal, not just a color.
    expect(
      items[0].querySelector('[data-testid="release-switcher-item-gate-fail"]')
        ?.textContent,
    ).toContain("Fail");
  });

  it("selecting a release item sets the snapshot via onSelectRelease(scan id)", async () => {
    mockedList.mockResolvedValue(TWO_RELEASES);
    const { onSelectRelease } = renderSwitcher();

    await waitFor(() => {
      expect(screen.getByTestId("release-switcher")).toBeEnabled();
    });
    await userEvent.click(screen.getByTestId("release-switcher"));
    await waitFor(() => {
      expect(screen.getAllByTestId("release-switcher-item")).toHaveLength(2);
    });

    const old = screen
      .getAllByTestId("release-switcher-item")
      .find((el) => el.getAttribute("data-scan-id") === "scan-old");
    await userEvent.click(old as HTMLElement);
    expect(onSelectRelease).toHaveBeenCalledWith("scan-old");
  });

  it("selecting 'Latest' clears the snapshot via onSelectLatest", async () => {
    mockedList.mockResolvedValue(TWO_RELEASES);
    const { onSelectLatest } = renderSwitcher({
      pinnedScanId: "scan-old",
      isHistorical: true,
    });

    await waitFor(() => {
      expect(screen.getByTestId("release-switcher")).toBeEnabled();
    });
    await userEvent.click(screen.getByTestId("release-switcher"));
    await waitFor(() => {
      expect(screen.getByTestId("release-switcher-latest")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("release-switcher-latest"));
    expect(onSelectLatest).toHaveBeenCalledTimes(1);
  });

  it("disables the trigger and shows 'No versions yet' when the project has no succeeded scan", async () => {
    mockedList.mockResolvedValue(listResponse([]));
    renderSwitcher({ latestScanId: null });

    await waitFor(() => {
      expect(screen.getByTestId("release-switcher-label").textContent).toContain(
        "No versions yet",
      );
    });
    expect(screen.getByTestId("release-switcher")).toBeDisabled();
  });

  it("falls back to the date label when a release carries no version name", async () => {
    mockedList.mockResolvedValue(
      listResponse([
        snapshot("scan-latest", {
          release: null,
          created_at: "2026-05-22T10:00:00Z",
        }),
      ]),
    );
    renderSwitcher({ latestScanId: "scan-latest" });

    await waitFor(() => {
      expect(screen.getByTestId("release-switcher-label").textContent).toContain(
        "2026",
      );
    });
  });
});
