/**
 * absoluteTime: the rules the seven private copies did not share (B3).
 */
import { describe, expect, it, vi } from "vitest";

import {
  ABSENT,
  formatAbsoluteDate,
  formatAbsoluteTime,
} from "@/lib/absoluteTime";

const INSTANT = "2026-08-14T00:30:00Z";

/** The options the helper asked Intl for, whatever zone this machine is in. */
function captureOptions(run: () => void): Intl.DateTimeFormatOptions | null {
  const real = Intl.DateTimeFormat;
  let seen: Intl.DateTimeFormatOptions | null = null;
  // A plain function, not an arrow: the helper calls this with `new`.
  function Capturing(
    locale?: string,
    options?: Intl.DateTimeFormatOptions,
  ): Intl.DateTimeFormat {
    seen = options ?? null;
    return new real(locale, options);
  }
  const spy = vi
    .spyOn(Intl, "DateTimeFormat")
    .mockImplementation(Capturing as unknown as typeof Intl.DateTimeFormat);
  try {
    run();
  } finally {
    spy.mockRestore();
  }
  return seen;
}

describe("formatAbsoluteTime", () => {
  it("renders the year, the month, the day, the hour, the minute and the zone", () => {
    // Pinned as parts rather than as one string so this does not become a
    // copy of the locale data, but pinned all the same: every other test
    // here compares the formatter against itself, so deleting the time from
    // it entirely left the whole suite green.
    const rendered = formatAbsoluteTime("2026-08-14T00:30:00Z", "en");

    expect(rendered).toContain("2026");
    // The month, asserted as a slash-delimited part so a zone whose offset
    // happens to make the hour read "08" cannot stand in for it.
    expect(rendered).toMatch(/\b08\//);
    // 00:30 UTC is the 14th east of Greenwich and the 13th west of it, and
    // the hour moves with it, so both are named rather than assumed.
    expect(rendered).toMatch(/\/(13|14)\//);
    expect(rendered).toMatch(/\d{1,2}:\d{2}/);
    expect(rendered).toMatch(/UTC[+-]\d/);
  });

  it("names the timezone it rendered in", () => {
    // Without this the same instant reads as two different wall-clock times
    // on two screens, with nothing to explain the gap: the audit log printed
    // UTC while every tooltip rendered in the browser's own zone.
    expect(formatAbsoluteTime(INSTANT, "en")).toMatch(/UTC[+-]/);
  });

  it("follows the locale it is given, not the browser's", () => {
    const en = formatAbsoluteTime(INSTANT, "en");
    const ko = formatAbsoluteTime(INSTANT, "ko");
    expect(en).not.toBe(ko);
  });

  it.each([
    ["null", null],
    ["undefined", undefined],
    ["empty", ""],
    ["not a date", "whenever"],
  ])("renders the placeholder for %s", (_label, value) => {
    expect(formatAbsoluteTime(value, "en")).toBe(ABSENT);
  });
});

describe("formatAbsoluteDate", () => {
  it("keeps a date-only value on its own day", () => {
    // `Date.parse("2026-08-14")` is midnight UTC. Rendering that in a zone
    // west of Greenwich would print the 13th, so a snapshot taken on the
    // 14th would be reported as a day older than it is.
    //
    // Asserted on the options handed to Intl rather than on the output: the
    // machine running this sits east of Greenwich, where the wrong code
    // prints the right day and a test on the string would pass either way.
    const options = captureOptions(() => formatAbsoluteDate("2026-08-14", "en"));
    expect(options).not.toBeNull();
    expect(options?.timeZone).toBe("UTC");
    expect(formatAbsoluteDate("2026-08-14", "en")).toContain("14");
  });

  it("does not pin an instant to UTC", () => {
    // An instant means a moment, and the reader's own day is the right day
    // to show it on.
    const options = captureOptions(() => formatAbsoluteDate(INSTANT, "en"));
    // Asserted non-null first: `options?.timeZone` on a spy that captured
    // nothing is undefined too, and would pass while checking nothing.
    expect(options).not.toBeNull();
    expect(options?.timeZone).toBeUndefined();
  });

  it("says nothing about a timezone", () => {
    // A date names a day, not a moment. A zone label would claim otherwise.
    expect(formatAbsoluteDate("2026-08-14", "en")).not.toMatch(/UTC|GMT/);
  });

  it("renders the year, the month and the day", () => {
    const rendered = formatAbsoluteDate("2026-08-14", "en");
    expect(rendered).toContain("2026");
    expect(rendered).toContain("14");
    expect(rendered).toMatch(/[A-Za-z]{3}/);
  });

  it("falls back rather than throwing on a malformed locale tag", () => {
    // `Intl.DateTimeFormat` throws a RangeError on an underscore tag, and a
    // locale reaches these helpers from stored preferences. A timestamp in
    // the wrong language beats a screen that fails to render.
    expect(() => formatAbsoluteDate("2026-08-14", "ko_KR")).not.toThrow();
    expect(() => formatAbsoluteTime(INSTANT, "ko_KR")).not.toThrow();
    expect(formatAbsoluteDate("2026-08-14", "ko_KR")).toContain("2026");
  });

  it("renders an instant as a day in the reader's own zone", () => {
    expect(formatAbsoluteDate(INSTANT, "en")).toMatch(/2026/);
  });

  it("renders the placeholder for a missing value", () => {
    expect(formatAbsoluteDate(null, "en")).toBe(ABSENT);
  });
});
