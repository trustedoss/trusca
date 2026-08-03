/**
 * ObligationDrawer — unit tests (PR #13).
 *
 * Read-only drawer for the obligations tab. Covers:
 *   - closed state does not fetch.
 *   - loading skeleton.
 *   - meta panel: parent license SPDX + category badge + kind chip.
 *   - obligation body: full text + reference link http(s) scheme guard.
 *   - non-http(s) link → plain text fallback (XSS scheme guard).
 *   - affected components cross-link → URL pivot to Components tab + drops
 *     the `?obligation=<id>` param so the drawer auto-closes.
 *   - error state surfaces RFC 7807 detail.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ObligationDetailResponse } from "@/features/projects/api/obligationsApi";
import { ObligationDrawer } from "@/features/projects/components/ObligationDrawer";
import i18n from "@/lib/i18n";
import { ProblemError } from "@/lib/problem";

vi.mock("@/features/projects/api/obligationsApi", async () => {
  return {
    listProjectObligations: vi.fn(),
    getObligation: vi.fn(),
    fetchProjectNotice: vi.fn(),
    KNOWN_OBLIGATION_KINDS: [
      "attribution",
      "notice",
      "source-disclosure",
      "copyleft",
      "modifications",
      "dynamic-linking",
      "no-endorsement",
    ] as const,
  };
});

import { getObligation } from "@/features/projects/api/obligationsApi";

const mockedGet = vi.mocked(getObligation);

function detail(
  overrides: Partial<ObligationDetailResponse> = {},
): ObligationDetailResponse {
  return {
    id: "obg-1",
    license_id: "lic-1",
    license_spdx_id: "MIT",
    license_name: "MIT License",
    license_category: "allowed",
    license_reference_url: "https://opensource.org/licenses/MIT",
    kind: "attribution",
    text: "Include the original copyright notice.",
    text_ko: null,
    text_truncated: false,
    link: "https://example.org/policy/attribution",
    affected_components: [
      {
        component_version_id: "cv-1",
        component_name: "react",
        version: "18.3.0",
      },
    ],
    affected_components_truncated: false,
    affected_components_total: 1,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
    ...overrides,
  };
}

function URLProbe() {
  const [params] = useSearchParams();
  return (
    <div
      data-testid="url-probe"
      data-tab={params.get("tab") ?? ""}
      data-drawer={params.get("drawer") ?? ""}
      data-obligation={params.get("obligation") ?? ""}
    />
  );
}

function renderDrawer(
  obligationId: string | null,
  open = true,
  onOpenChange: (open: boolean) => void = () => {},
  initialEntries: string[] = ["/projects/p1?obligation=obg-1"],
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>
        <ObligationDrawer
          open={open}
          projectId="p1"
          obligationId={obligationId}
          onOpenChange={onOpenChange}
        />
        <URLProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ObligationDrawer", () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders nothing when closed (no fetch)", () => {
    renderDrawer("any-id", false);
    expect(screen.queryByTestId("obligation-drawer")).not.toBeInTheDocument();
    expect(mockedGet).not.toHaveBeenCalled();
  });

  it("shows skeleton while the detail is loading", () => {
    mockedGet.mockReturnValue(new Promise(() => {}));
    renderDrawer("obg-1");
    expect(screen.getByTestId("obligation-drawer")).toBeInTheDocument();
    expect(screen.getByTestId("obligation-drawer-loading")).toBeInTheDocument();
  });

  it("renders the meta panel with parent license SPDX + category", async () => {
    mockedGet.mockResolvedValueOnce(detail());
    renderDrawer("obg-1");
    await waitFor(() => {
      expect(screen.getByTestId("obligation-drawer-meta")).toBeInTheDocument();
    });
    expect(screen.getByTestId("obligation-drawer-license-name").textContent).toBe(
      "MIT",
    );
    expect(screen.getByTestId("obligation-drawer-kind")).toBeInTheDocument();
  });

  it("renders the obligation body and a clickable http(s) reference link", async () => {
    mockedGet.mockResolvedValueOnce(detail());
    renderDrawer("obg-1");
    await waitFor(() => {
      expect(screen.getByTestId("obligation-drawer-text")).toBeInTheDocument();
    });
    expect(screen.getByTestId("obligation-drawer-text").textContent).toContain(
      "Include the original copyright notice.",
    );
    const ref = screen.getByTestId("obligation-drawer-reference");
    expect(ref.getAttribute("href")).toBe(
      "https://example.org/policy/attribution",
    );
    expect(ref.getAttribute("rel")).toContain("noopener");
  });

  it("renders non-http(s) link as plain text (XSS scheme guard)", async () => {
    mockedGet.mockResolvedValueOnce(
      detail({ link: "javascript:alert(1)" }),
    );
    renderDrawer("obg-1");
    await waitFor(() => {
      expect(screen.getByTestId("obligation-drawer-text")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("obligation-drawer-reference"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByTestId("obligation-drawer-reference-unsafe"),
    ).toBeInTheDocument();
  });

  it("omits the reference row entirely when link is null", async () => {
    mockedGet.mockResolvedValueOnce(detail({ link: null }));
    renderDrawer("obg-1");
    await waitFor(() => {
      expect(screen.getByTestId("obligation-drawer-text")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("obligation-drawer-reference"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("obligation-drawer-reference-unsafe"),
    ).not.toBeInTheDocument();
  });

  it("falls back to the raw kind string when the i18n key is unknown", async () => {
    mockedGet.mockResolvedValueOnce(detail({ kind: "experimental-kind" }));
    renderDrawer("obg-1");
    await waitFor(() => {
      expect(screen.getByTestId("obligation-drawer-kind")).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("obligation-drawer-kind").textContent,
    ).toContain("experimental-kind");
  });

  it("renders the affected components list and cross-links into Components tab", async () => {
    mockedGet.mockResolvedValueOnce(detail());
    const onOpenChange = vi.fn();
    renderDrawer("obg-1", true, onOpenChange);
    await waitFor(() => {
      expect(
        screen.getByTestId("obligation-drawer-affected-row"),
      ).toBeInTheDocument();
    });
    await userEvent.click(
      screen.getByTestId("obligation-drawer-affected-link"),
    );
    const probe = screen.getByTestId("url-probe");
    expect(probe.getAttribute("data-tab")).toBe("components");
    expect(probe.getAttribute("data-drawer")).toBe("cv-1");
    expect(probe.getAttribute("data-obligation")).toBe("");
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("renders an alert with the RFC 7807 detail on error", async () => {
    mockedGet.mockRejectedValueOnce(
      new ProblemError("not found", {
        status: 404,
        title: "Not Found",
        detail: "Obligation does not exist or is not visible to you.",
        problem: null,
      }),
    );
    renderDrawer("obg-1");
    await waitFor(() => {
      expect(screen.getByTestId("obligation-drawer-error")).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("obligation-drawer-error").textContent,
    ).toContain("Obligation does not exist or is not visible to you.");
  });

  // C1a — advisory Korean rendering with the English original disclosable.
  it("shows only the English text in English, with no original toggle", async () => {
    mockedGet.mockResolvedValueOnce(
      detail({
        text: "Include the original copyright notice.",
        text_ko: "원본 저작권 표시를 포함해야 합니다.",
      }),
    );
    renderDrawer("obg-1");
    await waitFor(() => {
      expect(screen.getByTestId("obligation-drawer-text")).toBeInTheDocument();
    });
    expect(screen.getByTestId("obligation-drawer-text").textContent).toBe(
      "Include the original copyright notice.",
    );
    // No original-language disclosure while reading English.
    expect(screen.queryByTestId("obligation-drawer-original")).toBeNull();
  });

  it("shows the Korean rendering in Korean and discloses the English original", async () => {
    await i18n.changeLanguage("ko");
    mockedGet.mockResolvedValueOnce(
      detail({
        text: "Include the original copyright notice.",
        text_ko: "원본 저작권 표시를 포함해야 합니다.",
      }),
    );
    renderDrawer("obg-1");
    await waitFor(() => {
      expect(screen.getByTestId("obligation-drawer-text")).toBeInTheDocument();
    });
    expect(screen.getByTestId("obligation-drawer-text").textContent).toBe(
      "원본 저작권 표시를 포함해야 합니다.",
    );
    // The authoritative English text stays reachable.
    expect(
      screen.getByTestId("obligation-drawer-original-text").textContent,
    ).toBe("Include the original copyright notice.");
  });

  it("falls back to English in Korean when no translation exists", async () => {
    await i18n.changeLanguage("ko");
    mockedGet.mockResolvedValueOnce(
      detail({ text: "A non-catalog obligation.", text_ko: null }),
    );
    renderDrawer("obg-1");
    await waitFor(() => {
      expect(screen.getByTestId("obligation-drawer-text")).toBeInTheDocument();
    });
    expect(screen.getByTestId("obligation-drawer-text").textContent).toBe(
      "A non-catalog obligation.",
    );
    expect(screen.queryByTestId("obligation-drawer-original")).toBeNull();
  });
});
