/**
 * useLicenseWaive — unit tests for the pure helpers + the 404-swallowing
 * team-policy read.
 *
 *   - `findComponentException` matches exactly on (spdx_id, component_purl) and
 *     does NOT treat a broad org-wide (component_purl: null) waiver as a
 *     per-component match.
 *   - `useTeamLicensePolicy` resolves a 404 ("no team policy, static fallback")
 *     to `null` rather than surfacing it as an error.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { LicensePolicyOut } from "@/lib/licensePoliciesApi";
import { ProblemError } from "@/lib/problem";

vi.mock("@/lib/licensePoliciesApi", async () => {
  return {
    addTeamLicenseException: vi.fn(),
    deleteTeamLicenseException: vi.fn(),
    getTeamPolicy: vi.fn(),
  };
});

import { getTeamPolicy } from "@/lib/licensePoliciesApi";
import {
  findComponentException,
  useTeamLicensePolicy,
} from "@/features/projects/api/useLicenseWaive";

const mockedGet = vi.mocked(getTeamPolicy);

function policy(): LicensePolicyOut {
  return {
    id: "p",
    organization_id: "o",
    team_id: "t",
    name: null,
    category_overrides: {},
    license_exceptions: [
      {
        spdx_id: "GPL-2.0-only",
        reason: "scoped",
        component_purl: "pkg:pypi/pyphen@0.14.0",
        expires_at: null,
      },
      {
        // Broad org-wide waiver — must NOT match a per-component lookup.
        spdx_id: "LGPL-3.0-only",
        reason: "broad",
        component_purl: null,
        expires_at: null,
      },
    ],
    unknown_license_category: "conditional",
    compound_operator_strategy: {
      AND: "most_restrictive",
      OR: "least_restrictive",
      WITH: "most_restrictive",
    },
    enabled: true,
    created_by_user_id: null,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
  };
}

afterEach(() => vi.clearAllMocks());

describe("findComponentException", () => {
  it("matches exactly on spdx_id + component_purl", () => {
    const ex = findComponentException(
      policy(),
      "GPL-2.0-only",
      "pkg:pypi/pyphen@0.14.0",
    );
    expect(ex?.reason).toBe("scoped");
  });

  it("does not match a broad (purl: null) waiver as per-component", () => {
    const ex = findComponentException(policy(), "LGPL-3.0-only", "pkg:any@1");
    expect(ex).toBeNull();
  });

  it("returns null for missing inputs", () => {
    expect(findComponentException(null, "MIT", "pkg:a@1")).toBeNull();
    expect(findComponentException(policy(), null, "pkg:a@1")).toBeNull();
    expect(findComponentException(policy(), "MIT", null)).toBeNull();
  });
});

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe("useTeamLicensePolicy", () => {
  it("swallows a 404 to null (no team policy → static fallback)", async () => {
    mockedGet.mockRejectedValue(
      new ProblemError("not found", {
        status: 404,
        title: "Not Found",
        detail: "no policy",
        problem: null,
      }),
    );
    const { result } = renderHook(() => useTeamLicensePolicy("t"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
  });

  it("returns the policy when present", async () => {
    mockedGet.mockResolvedValue(policy());
    const { result } = renderHook(() => useTeamLicensePolicy("t"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.license_exceptions).toHaveLength(2);
  });

  it("stays disabled (no fetch) without a team id", () => {
    renderHook(() => useTeamLicensePolicy(null), { wrapper });
    expect(mockedGet).not.toHaveBeenCalled();
  });
});
