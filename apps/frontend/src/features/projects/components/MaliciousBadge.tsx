// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * MaliciousBadge — known-malicious package signal (#26).
 *
 * Structural sibling of {@link EolBadge} and {@link KevBadge} — a colored dot
 * beside a literal text label, so colour is never the only signal. Rendered
 * ONLY for `malicious_state === "flagged"`; `clear` and not-yet-assessed
 * (`null`) both render nothing, because absence already reads as "no signal"
 * on the dense 40px row.
 *
 * Why this one is destructive-toned while EOL is `high`: EOL and currency are
 * maintenance risks you schedule. A malicious package is an attack already
 * inside the build — it was published to compromise whoever installs it, via
 * a typosquat, a hijacked maintainer account or an install-time payload. The
 * chip therefore sits at the top of the badge order in the component row.
 *
 * The tooltip states the action rather than naming the finding. "Remove this
 * package and rotate the credentials the build could reach" is the response;
 * an upgrade is NOT, which is the single most important thing to get across
 * and the reason this signal is kept off the severity axis entirely.
 *
 * `data-malicious-state` / `data-malicious-id` anchor e2e assertions so specs
 * never match translated copy.
 */

export interface MaliciousBadgeProps {
  /** The component's `malicious_state` — anything but `"flagged"` renders nothing. */
  maliciousState: string | null | undefined;
  /** OSV advisory id behind the verdict (e.g. "MAL-2025-47141"). */
  maliciousId?: string | null;
  /** Snapshot the verdict came from, `osv.dev@YYYY-MM-DD`. */
  maliciousSource?: string | null;
  /**
   * Render the advisory id inline next to the label (drawer / detail
   * surfaces). Default `false`: list rows keep the chip narrow.
   */
  showAdvisory?: boolean;
  className?: string;
}

export function MaliciousBadge({
  maliciousState,
  maliciousId,
  maliciousSource,
  showAdvisory = false,
  className,
}: MaliciousBadgeProps) {
  const { t } = useTranslation("project_detail");

  if (maliciousState !== "flagged") return null;

  // The snapshot date rides the tooltip because the claim is bounded: this
  // package is in the snapshot we shipped, not "malicious as of today".
  const tooltip = maliciousSource
    ? t("components.malicious.tooltip_with_source", { source: maliciousSource })
    : t("components.malicious.tooltip");

  return (
    <Badge
      tone="critical"
      data-testid="malicious-badge"
      data-malicious-state={maliciousState}
      data-malicious-id={maliciousId ?? undefined}
      title={tooltip}
      className={cn("gap-1.5 font-semibold", className)}
    >
      <span
        aria-hidden
        className="inline-block h-1.5 w-1.5 rounded-full bg-risk-critical"
      />
      <span>{t("components.malicious.label")}</span>
      {showAdvisory && maliciousId ? (
        <span
          className="font-mono text-[10px] font-normal tabular-nums"
          data-testid="malicious-badge-advisory"
        >
          {maliciousId}
        </span>
      ) : null}
    </Badge>
  );
}
