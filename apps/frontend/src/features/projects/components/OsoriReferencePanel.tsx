// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import type { OsoriReference } from "@/features/projects/api/licensesApi";

/**
 * What OSORI says about a license, shown next to — never instead of — this
 * deployment's own classification.
 *
 * The framing is the design. Our catalogue covers 52 licenses and drives the
 * build gate; OSORI describes about 670 and drives nothing. Presenting them as
 * one voice would let an outside opinion read as a verdict this product made.
 * So the panel is visually separate, labelled as reference material, and
 * carries its attribution inline — which ODC-By 1.0 requires anyway.
 *
 * It earns its space mainly on the licenses our catalogue does not classify,
 * where the drawer above it has a null summary and nothing else to say.
 */

export interface OsoriReferencePanelProps {
  osori: OsoriReference | null;
  /** True when our own catalogue already explains this license. */
  hasOwnSummary: boolean;
}

/** OSORI's source-disclosure vocabulary, ordered by reach. */
const DISCLOSURE_TONE: Record<string, "critical" | "high" | "medium" | "none"> =
  {
    NETWORK: "critical",
    EXECUTABLE: "high",
    LIBRARY: "medium",
    NONE: "none",
  };

export function OsoriReferencePanel({
  osori,
  hasOwnSummary,
}: OsoriReferencePanelProps) {
  const { t } = useTranslation("project_detail");
  if (!osori) return null;

  const hasSomethingToSay =
    osori.source_disclosure != null ||
    osori.notification_required != null ||
    osori.restrictions.length > 0;
  if (!hasSomethingToSay) return null;

  return (
    <section
      className="flex flex-col gap-2 rounded-md border border-dashed p-3"
      data-testid="license-drawer-osori"
      data-has-own-summary={hasOwnSummary}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {t("licenses.drawer.osori.title")}
        </span>
        {/* Said plainly rather than implied by the dashed border: this is
            someone else's reading, not the classification above. */}
        <span className="text-[11px] text-muted-foreground">
          {t("licenses.drawer.osori.reference_only")}
        </span>
      </div>

      {osori.source_disclosure ? (
        <div className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">
            {t("licenses.drawer.osori.source_disclosure")}
          </span>
          <Badge
            tone={DISCLOSURE_TONE[osori.source_disclosure] ?? "none"}
            data-testid="license-drawer-osori-disclosure"
          >
            {t(`licenses.drawer.osori.disclosure.${osori.source_disclosure}`, {
              defaultValue: osori.source_disclosure,
            })}
          </Badge>
        </div>
      ) : null}

      {osori.notification_required != null ? (
        <div className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">
            {t("licenses.drawer.osori.notification")}
          </span>
          <span data-testid="license-drawer-osori-notification">
            {osori.notification_required
              ? t("licenses.drawer.osori.required")
              : t("licenses.drawer.osori.not_required")}
          </span>
        </div>
      ) : null}

      {osori.restrictions.length > 0 ? (
        <div className="flex flex-col gap-1">
          <span className="text-xs text-muted-foreground">
            {t("licenses.drawer.osori.restrictions")}
          </span>
          <ul className="flex flex-wrap gap-1">
            {osori.restrictions.map((restriction) => (
              <li key={restriction}>
                <Badge data-testid="license-drawer-osori-restriction">
                  {restriction}
                </Badge>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* ODC-By 1.0 is attribution-only, and this is where the attribution
          has to appear for a reader who only ever sees this panel. */}
      <p className="text-[11px] text-muted-foreground">{osori.source}</p>
    </section>
  );
}
