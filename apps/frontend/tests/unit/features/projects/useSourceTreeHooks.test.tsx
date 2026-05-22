/**
 * useSourceTree / useSourceFile — unit tests (G3.3).
 *
 * Mocks the wire layer so the tests pin: query-key shape, the `enabled` gate
 * (no project id / no file path → no fetch), the success path, and the 404
 * no-retry policy (a missing preserved source shouldn't retry into a delayed
 * empty state).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  sourceFileKey,
  useSourceFile,
} from "@/features/projects/api/useSourceFile";
import {
  retryNon404,
  sourceTreeKey,
  useSourceTree,
} from "@/features/projects/api/useSourceTree";
import { ProblemError } from "@/lib/problem";

vi.mock("@/features/projects/api/sourceTreeApi", () => ({
  getSourceTree: vi.fn(),
  getSourceFile: vi.fn(),
}));

import {
  getSourceFile,
  getSourceTree,
} from "@/features/projects/api/sourceTreeApi";

const mockedTree = vi.mocked(getSourceTree);
const mockedFile = vi.mocked(getSourceFile);

function wrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe("query keys", () => {
  it("sourceTreeKey folds an undefined scan id to 'latest'", () => {
    expect(sourceTreeKey("p", undefined, "src")).toEqual([
      "projects",
      "p",
      "latest",
      "source-tree",
      "src",
    ]);
    expect(sourceTreeKey("p", "scan-9", "")).toEqual([
      "projects",
      "p",
      "scan-9",
      "source-tree",
      "",
    ]);
  });

  it("sourceFileKey folds null path to '' and undefined scan to 'latest'", () => {
    expect(sourceFileKey("p", undefined, null)).toEqual([
      "projects",
      "p",
      "latest",
      "source-file",
      "",
    ]);
  });
});

describe("useSourceTree", () => {
  beforeEach(() => mockedTree.mockReset());

  it("fetches when enabled and returns the page", async () => {
    mockedTree.mockResolvedValueOnce({
      scan_id: "scan-1",
      path: "",
      entries: [],
      total: 0,
      page: 1,
      size: 500,
    });
    const { result } = renderHook(() => useSourceTree("p", ""), {
      wrapper: wrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.scan_id).toBe("scan-1");
  });

  it("does not fetch when disabled", () => {
    renderHook(() => useSourceTree("p", "src", { enabled: false }), {
      wrapper: wrapper(),
    });
    expect(mockedTree).not.toHaveBeenCalled();
  });

  it("does not fetch without a project id", () => {
    renderHook(() => useSourceTree(undefined, "src"), { wrapper: wrapper() });
    expect(mockedTree).not.toHaveBeenCalled();
  });
});

describe("retryNon404", () => {
  function problem(status: number): ProblemError {
    return new ProblemError("err", {
      status,
      title: "x",
      detail: "x",
      problem: null,
    });
  }

  it("never retries a 404 (no preserved source is terminal)", () => {
    expect(retryNon404(0, problem(404))).toBe(false);
    expect(retryNon404(1, problem(404))).toBe(false);
  });

  it("retries a non-404 up to twice", () => {
    expect(retryNon404(0, problem(500))).toBe(true);
    expect(retryNon404(1, problem(500))).toBe(true);
    expect(retryNon404(2, problem(500))).toBe(false);
  });

  it("retries a plain (non-Problem) Error up to twice", () => {
    expect(retryNon404(0, new Error("network"))).toBe(true);
    expect(retryNon404(2, new Error("network"))).toBe(false);
  });
});

describe("useSourceFile", () => {
  beforeEach(() => mockedFile.mockReset());

  it("fetches when a path is selected", async () => {
    mockedFile.mockResolvedValueOnce({
      scan_id: "scan-1",
      path: "a.py",
      byte_size: 1,
      truncated: false,
      encoding: "utf-8",
      content: "x",
      license_matches: [],
    });
    const { result } = renderHook(() => useSourceFile("p", "a.py"), {
      wrapper: wrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.path).toBe("a.py");
  });

  it("does not fetch when no path is selected", () => {
    renderHook(() => useSourceFile("p", null), { wrapper: wrapper() });
    expect(mockedFile).not.toHaveBeenCalled();
  });
});
