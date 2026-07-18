import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * CurrencyBadge — version-currency signal, the lower-urgency sibling of
 * {@link EolBadge}.
 *
 * Structural mirror of EolBadge: a colored dot paired with the literal
 * "Outdated" text label so color is never the only signal. Rendered ONLY when
 * the component is behind the newest patch in its release line
 * (`currency_state === "outdated"`) — `current`, `unknown` and untracked
 * (`null`) all render nothing, because absence already reads as "no signal" on
 * the dense 40px list.
 *
 * Urgency ranking: EOL (`tone="high"`) means past end-of-life — no more
 * security fixes. Currency is a strictly lesser signal ("a newer patch
 * exists"), so this chip uses the Medium hue family (`medium` Badge tone +
 * `bg-risk-medium` dot) — never Critical/High visual weight. It carries the
 * literal "Outdated" label so it can never be confused with the severity
 * column's chip (the EolBadge / ReachabilityBadge rationale).
 *
 * The newest patch version (`currency_latest`) rides the tooltip always, and
 * inline on `showDate` surfaces (drawer / detail) alongside its release date.
 * `data-currency-state` / `data-currency-latest` anchor e2e assertions so the
 * spec never matches translated copy.
 */

export interface CurrencyBadgeProps {
  /** `currency_state` — anything but `"outdated"` renders nothing. */
  currencyState: string | null | undefined;
  /** Newest patch version in the release line, or `null`. */
  currencyLatest?: string | null;
  /**
   * Publish date (ISO) of `currencyLatest`, or `null`. Only shown inline on
   * `showDate` surfaces (drawer / detail).
   */
  currencyLatestReleaseDate?: string | null;
  /**
   * Render the latest patch inline next to the label (drawer / detail).
   * Default `false`: list rows keep the chip narrow, detail on tooltip only.
   */
  showDate?: boolean;
  className?: string;
}

export function CurrencyBadge({
  currencyState,
  currencyLatest,
  currencyLatestReleaseDate,
  showDate = false,
  className,
}: CurrencyBadgeProps) {
  const { t } = useTranslation("project_detail");

  if (currencyState !== "outdated") return null;

  const tooltip = currencyLatest
    ? t("components.currency.tooltip_with_version", { version: currencyLatest })
    : t("components.currency.tooltip");

  return (
    <Badge
      tone="medium"
      data-testid="currency-badge"
      data-currency-state={currencyState}
      data-currency-latest={currencyLatest ?? undefined}
      title={tooltip}
      className={cn("gap-1.5 font-semibold", className)}
    >
      <span
        aria-hidden
        className="inline-block h-1.5 w-1.5 rounded-full bg-risk-medium"
      />
      <span>{t("components.currency.label")}</span>
      {showDate && currencyLatest ? (
        <span
          className="font-mono text-[10px] font-normal tabular-nums"
          data-testid="currency-badge-latest"
        >
          {currencyLatestReleaseDate
            ? t("components.currency.latest_with_date", {
                version: currencyLatest,
                date: currencyLatestReleaseDate,
              })
            : t("components.currency.latest", { version: currencyLatest })}
        </span>
      ) : null}
    </Badge>
  );
}
