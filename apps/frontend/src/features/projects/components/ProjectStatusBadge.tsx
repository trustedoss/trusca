// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useTranslation } from "react-i18next";

import { StatusPill } from "@/components/primer/status-pill";
import type { ScanStatus } from "@/lib/projectsApi";

/**
 * ProjectStatusBadge — Phase 2 PR #9 task 2.11.
 *
 * Maps a scan status to a tinted Badge plus a text label. CLAUDE.md's
 * accessibility rule "color is not the only signal" — every badge carries
 * a translated word in addition to the dot.
 */

export interface ProjectStatusBadgeProps {
  /** Scan status, or null when the project has never been scanned. */
  status: ScanStatus | "idle" | null;
  className?: string;
}

type Tone = "neutral" | "progress" | "warning" | "danger" | "success";

interface Visual {
  tone: Tone;
  i18nKey: string;
  testid: string;
}

/**
 * Exported for the PR-6 catalog-mirror contract test
 * (`tests/unit/contracts/catalogMirrors.test.ts`): every ScanStatus must map
 * to its OWN `status.<status>` i18n key — `cancelled` silently borrowed
 * `status.failed` for two releases because nothing pinned the pairing.
 */
export function visualFor(status: ProjectStatusBadgeProps["status"]): Visual {
  switch (status) {
    case "queued":
      return { tone: "neutral", i18nKey: "status.queued", testid: "queued" };
    case "running":
      return { tone: "progress", i18nKey: "status.running", testid: "running" };
    case "succeeded":
      return {
        tone: "success",
        i18nKey: "status.succeeded",
        testid: "succeeded",
      };
    case "failed":
      return { tone: "danger", i18nKey: "status.failed", testid: "failed" };
    case "cancelled":
      // PR-6 catalog guard fix: was `status.failed` — a cancelled scan
      // rendered the word "Failed". `status.cancelled` existed in both
      // locales all along.
      return {
        tone: "warning",
        i18nKey: "status.cancelled",
        testid: "cancelled",
      };
    case "idle":
    case null:
    default:
      return { tone: "neutral", i18nKey: "status.idle", testid: "idle" };
  }
}

export function ProjectStatusBadge({
  status,
  className,
}: ProjectStatusBadgeProps) {
  const { t } = useTranslation("projects");
  const visual = visualFor(status);
  return (
    <StatusPill
      tone={visual.tone}
      data-testid={`project-status-${visual.testid}`}
      data-status={status ?? "idle"}
      className={className}
    >
      {t(visual.i18nKey)}
    </StatusPill>
  );
}
