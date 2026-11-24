import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import {
  collectEmptyColumns,
  effectiveColumns,
  useEmptyColumns,
  usePinnedColumns,
  type EmptyColumnRule,
} from "@/hooks/useEmptyColumns";

interface Row {
  a: number | null;
  b: number | null;
}
const RULES: readonly EmptyColumnRule<Row>[] = [
  { id: "a", hasValue: (r) => r.a != null },
  { id: "b", hasValue: (r) => r.b != null },
];

describe("collectEmptyColumns", () => {
  it("decides nothing while there are no rows", () => {
    expect(collectEmptyColumns([], RULES, new Set()).size).toBe(0);
  });

  it("reports the columns no row has a value for", () => {
    const empty = collectEmptyColumns(
      [
        { a: 1, b: null },
        { a: null, b: null },
      ],
      RULES,
      new Set(),
    );
    expect([...empty]).toEqual(["b"]);
  });

  it("remembers a column that has had data, so a later empty page cannot hide it", () => {
    const seen = new Set<string>();
    collectEmptyColumns([{ a: 1, b: null }], RULES, seen);
    const empty = collectEmptyColumns([{ a: null, b: null }], RULES, seen);
    expect([...empty]).toEqual(["b"]);
    expect(seen.has("a")).toBe(true);
  });
});

describe("effectiveColumns", () => {
  it("removes empty columns unless they are pinned", () => {
    const out = effectiveColumns(
      new Set(["x", "a", "b"]),
      new Set(["a", "b"]),
      new Set(["b"]),
    );
    expect([...out].sort()).toEqual(["b", "x"]);
  });

  it("does not add a column the user turned off", () => {
    expect(
      effectiveColumns(new Set(["x"]), new Set(), new Set(["a"])).has("a"),
    ).toBe(false);
  });
});

describe("useEmptyColumns", () => {
  it("keeps a column visible across renders once data appeared, and resets on a new key", () => {
    const { result, rerender } = renderHook(
      ({ rows, k }: { rows: Row[]; k: string }) =>
        useEmptyColumns(rows, RULES, k),
      { initialProps: { rows: [{ a: 1, b: null }] as Row[], k: "p1" } },
    );
    expect([...result.current]).toEqual(["b"]);
    rerender({ rows: [{ a: null, b: null }], k: "p1" });
    expect([...result.current]).toEqual(["b"]);
    rerender({ rows: [{ a: null, b: null }], k: "p2" });
    expect([...result.current].sort()).toEqual(["a", "b"]);
  });
});

describe("usePinnedColumns", () => {
  beforeEach(() => window.localStorage.clear());

  it("pins only columns added in a menu change, and persists them", () => {
    const { result } = renderHook(() => usePinnedColumns("column-pinned:t"));
    act(() => result.current.notePicked(new Set(["x"]), new Set(["x", "a"])));
    expect([...result.current.pinned]).toEqual(["a"]);
    expect(window.localStorage.getItem("column-pinned:t")).toBe('["a"]');
    act(() => result.current.notePicked(new Set(["x", "a"]), new Set(["x"])));
    expect([...result.current.pinned]).toEqual(["a"]);
  });

  it("reads earlier pins and ignores a corrupt entry", () => {
    window.localStorage.setItem("column-pinned:t", '["a"]');
    const first = renderHook(() => usePinnedColumns("column-pinned:t"));
    expect([...first.result.current.pinned]).toEqual(["a"]);
    window.localStorage.setItem("column-pinned:t", "{oops");
    const second = renderHook(() => usePinnedColumns("column-pinned:t"));
    expect(second.result.current.pinned.size).toBe(0);
  });
});
