/**
 * RelativeTime — unit tests (M-19 follow-up, PR-9).
 *
 * The whole point of the component is to make the absolute-time tooltip
 * structural: any relative display rendered through it is guaranteed to expose
 * the absolute instant on hover plus a semantic `<time dateTime>` element. The
 * previous regression (M-19) was that the dashboard "last scan" and the
 * approval-queue requested-date rendered the relative string with NO `title`.
 *
 * Coverage:
 *   - Renders a <time> with both `title` (absolute) and `dateTime` (ISO), and a
 *     body that is the relative string — never the raw ISO.
 *   - null / undefined / empty → em-dash, and crucially NO title / dateTime.
 *   - Unparseable input → em-dash, no title.
 *   - An explicit `locale` flows through to both relative body and the
 *     absolute title.
 *   - Regression guard: a relative display always carries an absolute-time
 *     title whenever it carries a relative body (the invariant M-19 broke).
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import RelativeTime from "@/components/RelativeTime";
import { formatAbsoluteTime } from "@/lib/absoluteTime";
import { formatRelativeToNow } from "@/lib/relativeTime";

// A fixed instant well in the past so the relative bucket is stable ("years
// ago") regardless of when the suite runs.
const PAST_ISO = "2000-01-02T03:04:05Z";

describe("RelativeTime", () => {
  it("renders a <time> carrying both the absolute title and the ISO dateTime", () => {
    render(<RelativeTime value={PAST_ISO} locale="en" data-testid="rt" />);

    const el = screen.getByTestId("rt");
    expect(el.tagName).toBe("TIME");

    // dateTime is the raw ISO wire value (machine-readable).
    expect(el.getAttribute("dateTime")).toBe(PAST_ISO);

    // title is the locale-formatted absolute instant — must be present and
    // must NOT be empty. B3: it now comes from the shared formatter, so it
    // names the zone it rendered in.
    const title = el.getAttribute("title");
    expect(title).toBeTruthy();
    expect(title).toBe(formatAbsoluteTime(PAST_ISO, "en"));
    expect(title).toMatch(/UTC[+-]/);

    // The visible body is the relative string, never the raw ISO.
    expect(el.textContent).not.toContain("2000-01-02T");
    expect(el.textContent?.length).toBeGreaterThan(0);
  });

  it("renders the em-dash with no title / dateTime for null", () => {
    render(<RelativeTime value={null} data-testid="rt-null" />);

    const el = screen.getByTestId("rt-null");
    expect(el.textContent).toBe("—");
    expect(el.getAttribute("title")).toBeNull();
    expect(el.getAttribute("dateTime")).toBeNull();
  });

  it("renders the em-dash with no title for undefined and empty string", () => {
    const { rerender } = render(
      <RelativeTime value={undefined} data-testid="rt-undef" />,
    );
    expect(screen.getByTestId("rt-undef").textContent).toBe("—");
    expect(screen.getByTestId("rt-undef").getAttribute("title")).toBeNull();

    rerender(<RelativeTime value="" data-testid="rt-undef" />);
    expect(screen.getByTestId("rt-undef").textContent).toBe("—");
    expect(screen.getByTestId("rt-undef").getAttribute("title")).toBeNull();
  });

  it("renders the em-dash with no title for an unparseable value", () => {
    render(<RelativeTime value="not-a-date" data-testid="rt-bad" />);

    const el = screen.getByTestId("rt-bad");
    expect(el.textContent).toBe("—");
    expect(el.getAttribute("title")).toBeNull();
  });

  it("forwards an explicit locale to both the body and the absolute title", () => {
    render(<RelativeTime value={PAST_ISO} locale="de" data-testid="rt-de" />);

    const el = screen.getByTestId("rt-de");
    // German locale title differs from the English-formatted one.
    expect(el.getAttribute("title")).toBe(formatAbsoluteTime(PAST_ISO, "de"));
    expect(el.getAttribute("title")).not.toBe(
      formatAbsoluteTime(PAST_ISO, "en"),
    );
  });

  it("puts the absolute instant in front when asked, and the relative behind", () => {
    // B3: for the tables where the instant is the content rather than the
    // age. A column of "3 hours ago" cannot be read against a timeline.
    render(
      <RelativeTime
        value={PAST_ISO}
        locale="en"
        display="absolute"
        data-testid="rt-abs"
      />,
    );

    const el = screen.getByTestId("rt-abs");
    expect(el.textContent).toBe(formatAbsoluteTime(PAST_ISO, "en"));
    expect(el.textContent).toMatch(/UTC[+-]/);
    // The relative form is still one hover away, and the machine-readable
    // instant is still on the element.
    expect(el.getAttribute("title")).toBe(formatRelativeToNow(PAST_ISO, "en"));
    expect(el.getAttribute("dateTime")).toBe(PAST_ISO);
  });

  it("shows the relative form in front unless told otherwise", () => {
    render(<RelativeTime value={PAST_ISO} locale="en" data-testid="rt-def" />);

    const el = screen.getByTestId("rt-def");
    expect(el.textContent).toBe(formatRelativeToNow(PAST_ISO, "en"));
  });

  it("forwards className onto the time element", () => {
    render(
      <RelativeTime
        value={PAST_ISO}
        className="text-xs custom-flag"
        data-testid="rt-class"
      />,
    );
    expect(screen.getByTestId("rt-class").className).toContain("custom-flag");
  });

  it("regression guard: a rendered relative body always carries an absolute title", () => {
    // The invariant M-19 broke — every non-empty relative display must expose
    // the absolute instant on hover. Render across several instants/locales.
    const cases: Array<{ value: string; locale?: string }> = [
      { value: PAST_ISO, locale: "en" },
      { value: "2026-05-01T00:00:00Z", locale: "ko" },
      { value: new Date(Date.now() - 3 * 60 * 1000).toISOString() },
      { value: new Date(Date.now() + 90 * 60 * 1000).toISOString(), locale: "en" },
    ];

    cases.forEach(({ value, locale }, i) => {
      render(
        <RelativeTime value={value} locale={locale} data-testid={`guard-${i}`} />,
      );
      const el = screen.getByTestId(`guard-${i}`);
      // Has a non-empty relative body...
      expect(el.textContent?.length).toBeGreaterThan(0);
      expect(el.textContent).not.toBe("—");
      // ...therefore MUST carry the absolute-time title and the dateTime.
      expect(el.getAttribute("title")).toBeTruthy();
      expect(el.getAttribute("dateTime")).toBe(value);
    });
  });
});
