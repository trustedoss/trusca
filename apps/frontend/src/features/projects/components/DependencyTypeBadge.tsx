import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * DependencyTypeBadge — W2 #31.
 *
 * BD-style "Dependency type" facet shown next to the component name. Surfaces
 * three buckets:
 *
 *   - `direct: true` → "Direct" (strong tone) with an emerald dot. Direct
 *     deps are what the project explicitly pulls in; engineers care about
 *     them first because they own the version bump.
 *   - `direct: false && depth >= 2` → "Transitive" (muted tone).
 *   - `depth == null` → em-dash placeholder (the scan recorded no graph;
 *     "—" is visually muted so it doesn't read as a real classification).
 *
 * Color is never the only signal: every badge carries a localized label.
 * The depth value is exposed via `data-depth` so e2e/unit harnesses can
 * assert on the shallowest-path number without parsing the tooltip text.
 */

export interface DependencyTypeBadgeProps {
  /** ``true`` when the backend marked depth == 1 on any reaching path. */
  direct: boolean;
  /** Graph depth from the scanned root; ``null`` when the scan has no graph. */
  depth: number | null;
  className?: string;
}

export function DependencyTypeBadge({
  direct,
  depth,
  className,
}: DependencyTypeBadgeProps) {
  const { t } = useTranslation("project_detail");

  // depth-null bucket — render a muted em-dash, NOT "Transitive". Conflating
  // the two would lie about cdxgen output: "no graph captured" ≠ "depth 2+".
  if (depth == null) {
    return (
      <Badge
        variant="muted"
        data-testid="dependency-type-badge"
        data-dependency-type="unknown"
        data-depth=""
        aria-label={t("components.badge.dependency_type.aria_unknown")}
        className={cn("gap-1.5", className)}
      >
        <span
          aria-hidden
          className="inline-block h-1.5 w-1.5 rounded-full bg-muted-foreground/40"
        />
        <span>{t("components.badge.dependency_type.unknown")}</span>
      </Badge>
    );
  }

  if (direct) {
    return (
      <Badge
        tone="success"
        data-testid="dependency-type-badge"
        data-dependency-type="direct"
        data-depth={depth}
        aria-label={t("components.badge.dependency_type.aria_direct")}
        className={cn("gap-1.5", className)}
      >
        <span
          aria-hidden
          className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-500"
        />
        <span>{t("components.badge.dependency_type.direct")}</span>
      </Badge>
    );
  }

  return (
    <Badge
      variant="muted"
      data-testid="dependency-type-badge"
      data-dependency-type="transitive"
      data-depth={depth}
      aria-label={t("components.badge.dependency_type.aria_transitive", {
        depth,
      })}
      className={cn("gap-1.5", className)}
    >
      <span
        aria-hidden
        className="inline-block h-1.5 w-1.5 rounded-full bg-muted-foreground/60"
      />
      <span>{t("components.badge.dependency_type.transitive")}</span>
    </Badge>
  );
}
