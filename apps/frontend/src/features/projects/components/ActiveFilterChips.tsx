import { X } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { LicenseCategoryName } from "@/features/projects/api/projectDetailApi";
import { cn } from "@/lib/utils";

/**
 * ActiveFilterChips — W4-B #17 / #19 shared primitive.
 *
 * When chart deep-links (#16) populate `?severity=` or `?license_category=`
 * the user no longer sees a MultiSelect that exposes the filter — so we
 * surface each selected value as a removable chip just above the table.
 *
 * One chip per selected value; click the X to remove that single value (and
 * call back into the parent so it can re-mirror the now-shorter list into
 * URL params). Bag-style filters only; tri-state / scalar filters (Direct,
 * EPSS threshold) keep their dedicated affordance.
 *
 * Accessibility:
 *   - Each remove button has an `aria-label` keyed off the chip's facet +
 *     value so screen readers announce "Remove severity critical".
 *   - The container has `role="region"` + label so the chips are
 *     navigable as a group.
 */

/**
 * Severity values used by Components and Vulnerabilities differ slightly
 * (`none` vs `unknown`), so we keep this generic on the caller's enum.
 * Each tab passes its own array element type and gets type-safe callbacks
 * back; the chip only ever string-renders the value, so no narrowing is
 * needed inside.
 */
export interface ActiveFilterChipsProps<S extends string = string> {
  /** Currently-selected severity values. Empty array hides the severity facet. */
  severity?: S[];
  onSeverityChange?: (next: S[]) => void;
  /** Currently-selected license-category values. */
  licenseCategory?: LicenseCategoryName[];
  onLicenseCategoryChange?: (next: LicenseCategoryName[]) => void;
  className?: string;
}

export function ActiveFilterChips<S extends string = string>({
  severity,
  onSeverityChange,
  licenseCategory,
  onLicenseCategoryChange,
  className,
}: ActiveFilterChipsProps<S>) {
  const { t } = useTranslation("project_detail");
  const sev = severity ?? [];
  const lic = licenseCategory ?? [];
  const hasChips = sev.length > 0 || lic.length > 0;

  if (!hasChips) {
    return null;
  }

  return (
    <div
      role="region"
      aria-label={t("active_filters.aria_label")}
      data-testid="active-filter-chips"
      className={cn(
        "flex flex-wrap items-center gap-2 border-b bg-muted/30 px-4 py-2",
        className,
      )}
    >
      <span className="text-xs uppercase tracking-wide text-muted-foreground">
        {t("active_filters.heading")}
      </span>
      {sev.map((value) => (
        <Chip
          key={`severity-${value}`}
          facet="severity"
          label={t(`active_filters.severity_chip_label`, {
            value: t(`severity.${value}`, { defaultValue: value }),
          })}
          ariaLabel={t(`active_filters.clear_severity_aria`, {
            value: t(`severity.${value}`, { defaultValue: value }),
          })}
          value={value}
          onClear={() => onSeverityChange?.(sev.filter((v) => v !== value))}
        />
      ))}
      {lic.map((value) => (
        <Chip
          key={`license-${value}`}
          facet="license_category"
          label={t(`active_filters.license_chip_label`, {
            value: t(`license_category.${value}`, { defaultValue: value }),
          })}
          ariaLabel={t(`active_filters.clear_license_aria`, {
            value: t(`license_category.${value}`, { defaultValue: value }),
          })}
          value={value}
          onClear={() =>
            onLicenseCategoryChange?.(lic.filter((v) => v !== value))
          }
        />
      ))}
    </div>
  );
}

interface ChipProps {
  facet: string;
  value: string;
  label: string;
  ariaLabel: string;
  onClear: () => void;
}

function Chip({ facet, value, label, ariaLabel, onClear }: ChipProps) {
  return (
    <span
      data-testid="active-filter-chip"
      data-facet={facet}
      data-value={value}
      className="inline-flex items-center gap-1 rounded-full border border-input bg-background pl-2 pr-1 py-0.5 text-xs"
    >
      <span>{label}</span>
      <button
        type="button"
        onClick={onClear}
        aria-label={ariaLabel}
        data-testid="active-filter-chip-clear"
        className={cn(
          "inline-flex h-4 w-4 items-center justify-center rounded-full",
          "text-muted-foreground hover:bg-muted hover:text-foreground",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
      >
        <X className="h-3 w-3" aria-hidden />
      </button>
    </span>
  );
}
