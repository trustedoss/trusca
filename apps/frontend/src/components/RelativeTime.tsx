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
 * Absolute tooltip format mirrors the notification inbox: the locale-aware
 * `Date.prototype.toLocaleString`, matching the format auditors already see
 * elsewhere in the product.
 */
import { useTranslation } from "react-i18next";

import { formatRelativeToNow } from "@/lib/relativeTime";

const FALLBACK = "—";

interface Props {
  /** ISO-8601 instant. `null` / `undefined` / empty → em-dash, no tooltip. */
  value: string | null | undefined;
  /**
   * Optional BCP-47 locale override. When omitted the active i18n language is
   * used, so the relative text and the absolute tooltip share one locale.
   */
  locale?: string;
  className?: string;
  /** Forwarded onto the rendered element (e.g. for harness/test hooks). */
  "data-testid"?: string;
}

/**
 * Resolve the absolute-instant tooltip. Returns `undefined` for unparseable
 * input so the DOM omits the attribute entirely rather than showing "Invalid
 * Date".
 */
function absoluteTitle(
  value: string | null | undefined,
  locale: string | undefined,
): string | undefined {
  if (value == null || value === "") return undefined;
  const ts = Date.parse(value);
  if (Number.isNaN(ts)) return undefined;
  return new Date(ts).toLocaleString(locale);
}

export default function RelativeTime({
  value,
  locale,
  className,
  "data-testid": dataTestId,
}: Props) {
  const { i18n } = useTranslation();
  const resolvedLocale = locale ?? i18n.resolvedLanguage ?? i18n.language;

  const title = absoluteTitle(value, resolvedLocale);
  const body = formatRelativeToNow(value, resolvedLocale);

  // No parseable instant → render the bare em-dash placeholder. We still emit
  // a <time> wrapper for layout/testid stability, but with neither dateTime
  // nor title so the markup stays honest about the missing value.
  if (title === undefined) {
    return (
      <time className={className} data-testid={dataTestId}>
        {FALLBACK}
      </time>
    );
  }

  return (
    <time
      className={className}
      dateTime={value ?? undefined}
      title={title}
      data-testid={dataTestId}
    >
      {body}
    </time>
  );
}
