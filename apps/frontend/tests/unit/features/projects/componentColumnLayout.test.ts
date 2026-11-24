import { describe, expect, it } from "vitest";

import {
  COMPONENT_COLUMN_WIDTH_PX,
  componentTrackMinWidth,
} from "@/features/projects/lib/componentColumnLayout";

describe("componentTrackMinWidth", () => {
  const all = new Set(Object.keys(COMPONENT_COLUMN_WIDTH_PX));

  it("is the old fixed floor with every column on", () => {
    expect(componentTrackMinWidth(all)).toBe(1184);
  });

  it("always counts the required version column", () => {
    // 48 padding + 120 name + 112 version + 1 gutter of 12.
    expect(componentTrackMinWidth(new Set())).toBe(48 + 120 + 112 + 12);
  });

  it("shrinks by a hidden column's width and its gutter", () => {
    const withoutType = new Set([...all].filter((id) => id !== "type"));
    expect(
      componentTrackMinWidth(all) - componentTrackMinWidth(withoutType),
    ).toBe(96 + 12);
  });
});
