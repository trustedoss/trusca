/**
 * CurrencyBadge — unit tests (version-currency signal, EolBadge sibling).
 *
 * Validates the render contract, mirroring the KevBadge / EolBadge tests:
 *   - currency_state="outdated" → "Outdated" badge with a colored dot + label.
 *   - current / unknown / null  → nothing (absence reads as "on latest / not
 *                                 tracked" — the EolBadge contract).
 *   - currencyLatest            → folded into the title tooltip; rendered
 *                                 inline only when `showDate` (drawer surface).
 *   - lower urgency than EOL    → Medium tone, never Critical/High.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CurrencyBadge } from "@/features/projects/components/CurrencyBadge";

describe("CurrencyBadge", () => {
  it("renders an Outdated badge with a dot + text label for outdated", () => {
    render(
      <CurrencyBadge currencyState="outdated" currencyLatest="2.5.1" />,
    );
    const badge = screen.getByTestId("currency-badge");
    // Color is paired with the literal "Outdated" label + dot, never color-only.
    expect(badge.textContent).toContain("Outdated");
    expect(badge.querySelector("span[aria-hidden]")).toBeInTheDocument();
    // e2e anchors: state + latest patch as data-* attributes.
    expect(badge).toHaveAttribute("data-currency-state", "outdated");
    expect(badge).toHaveAttribute("data-currency-latest", "2.5.1");
  });

  it("uses a Medium tone (lower urgency than EOL's High), never Critical/High", () => {
    render(
      <CurrencyBadge currencyState="outdated" currencyLatest="2.5.1" />,
    );
    const badge = screen.getByTestId("currency-badge");
    // Medium hue family for the dot; the chip never escalates to the
    // critical/high risk fills EOL / KEV use.
    expect(badge.querySelector(".bg-risk-medium")).toBeInTheDocument();
    expect(badge.className).not.toMatch(/risk-critical|risk-high/);
  });

  it("renders nothing for current", () => {
    const { container } = render(
      <CurrencyBadge currencyState="current" currencyLatest="2.5.1" />,
    );
    expect(screen.queryByTestId("currency-badge")).not.toBeInTheDocument();
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for unknown", () => {
    const { container } = render(
      <CurrencyBadge currencyState="unknown" currencyLatest={null} />,
    );
    expect(screen.queryByTestId("currency-badge")).not.toBeInTheDocument();
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when currency_state is null (untracked)", () => {
    const { container } = render(<CurrencyBadge currencyState={null} />);
    expect(screen.queryByTestId("currency-badge")).not.toBeInTheDocument();
    expect(container).toBeEmptyDOMElement();
  });

  it("folds the latest patch into the tooltip without rendering it inline", () => {
    render(
      <CurrencyBadge currencyState="outdated" currencyLatest="2.5.1" />,
    );
    const badge = screen.getByTestId("currency-badge");
    // The latest patch rides the title (accessible via hover) but the compact
    // list row keeps the chip narrow — no inline text node.
    expect(badge.getAttribute("title")).toContain("2.5.1");
    expect(
      screen.queryByTestId("currency-badge-latest"),
    ).not.toBeInTheDocument();
  });

  it("renders the latest patch + release date inline when showDate is set (drawer)", () => {
    render(
      <CurrencyBadge
        currencyState="outdated"
        currencyLatest="2.5.1"
        currencyLatestReleaseDate="2026-03-01"
        showDate
      />,
    );
    const inline = screen.getByTestId("currency-badge-latest");
    expect(inline.textContent).toContain("2.5.1");
    expect(inline.textContent).toContain("2026-03-01");
  });

  it("renders the badge with a version-less tooltip and no inline text when latest is unknown", () => {
    render(
      <CurrencyBadge
        currencyState="outdated"
        currencyLatest={null}
        showDate
      />,
    );
    const badge = screen.getByTestId("currency-badge");
    expect(badge.textContent).toContain("Outdated");
    // No latest patch → the generic tooltip (no {{version}}) and no inline node.
    expect(badge.getAttribute("title")).not.toContain("{{");
    expect(
      screen.queryByTestId("currency-badge-latest"),
    ).not.toBeInTheDocument();
  });

  it("shows the latest patch inline without a date when the release date is null", () => {
    render(
      <CurrencyBadge
        currencyState="outdated"
        currencyLatest="2.5.1"
        currencyLatestReleaseDate={null}
        showDate
      />,
    );
    const inline = screen.getByTestId("currency-badge-latest");
    expect(inline.textContent).toContain("2.5.1");
    // No parenthesised date when the feed is undated.
    expect(inline.textContent).not.toContain("(");
  });
});
