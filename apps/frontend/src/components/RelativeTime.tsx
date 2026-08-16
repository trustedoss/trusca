// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * RelativeTime — shared relative-timestamp display (M-19 follow-up).
 *
 * `formatRelativeToNow` is a string helper, so before this component every
 * call site decided on its own whether to attach the absolute instant as a
 * `title` tooltip. M-19 (#365) only wired that tooltip into the vulnerability
 * "Discovered" cell and the notification inbox; the dashboard "last scan N
 * hours ago" and the approval-queue requested-date had no `title` at all.
 *
 * This component makes the absolute-time tooltip structural: any relative
 * display that renders through `<RelativeTime>` is guaranteed to expose the
 * absolute instant on hover and to emit a semantic `<time dateTime>` element
 * for assistive tech and machine readers. The relative text itself still
 * comes from the shared `formatRelativeToNow` helper — no logic is duplicated.
 *
 * B3: the absolute form comes from `lib/absoluteTime`, shared with the
 * screens that render an absolute instant directly, and it names the timezone
 * it is in. Before that this tooltip used a bare `toLocaleString`, which
 * renders in the browser's zone and says nothing about which one, while the
 * audit log printed its raw UTC instant beside it, so the same moment showed
 * as two different wall-clock times with nothing to explain the gap.
 */
import { useTranslation } from "react-i18next";

import { ABSENT, formatAbsoluteTime } from "@/lib/absoluteTime";
import { formatRelativeToNow } from "@/lib/relativeTime";

interface Props {
  /** ISO-8601 instant. `null` / `undefined` / empty → em-dash, no tooltip. */
  value: string | null | undefined;
  /**
   * Optional BCP-47 locale override. When omitted the active i18n language is
   * used, so the relative text and the absolute tooltip share one locale.
   */
  locale?: string;
  /**
   * Which form to put in front of the reader; the other becomes the tooltip.
   *
   * `relative` (the default) is right where the age is the point: "last
   * scanned 3 hours ago". `absolute` is for the tables where the exact
   * instant IS the content and a column of "3 hours ago" cannot be read
   * against a timeline: the audit log, and the per-project scan history.
   */
  display?: "relative" | "absolute";
  className?: string;
  /** Forwarded onto the rendered element (e.g. for harness/test hooks). */
  "data-testid"?: string;
}

export default function RelativeTime({
  value,
  locale,
  display = "relative",
  className,
  "data-testid": dataTestId,
}: Props) {
  const { i18n } = useTranslation();
  const resolvedLocale = locale ?? i18n.resolvedLanguage ?? i18n.language;

  const absolute = formatAbsoluteTime(value, resolvedLocale);

  // No parseable instant → render the bare em-dash placeholder. We still emit
  // a <time> wrapper for layout/testid stability, but with neither dateTime
  // nor title so the markup stays honest about the missing value.
  if (absolute === ABSENT) {
    return (
      <time className={className} data-testid={dataTestId}>
        {ABSENT}
      </time>
    );
  }

  const relative = formatRelativeToNow(value, resolvedLocale);
  const showsAbsolute = display === "absolute";

  return (
    <time
      className={className}
      dateTime={value ?? undefined}
      title={showsAbsolute ? relative : absolute}
      data-testid={dataTestId}
    >
      {showsAbsolute ? absolute : relative}
    </time>
  );
}
