import { History } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * SnapshotBanner — feature #28 Phase 1 (release snapshot viewing).
 *
 * Prominent read-only notice rendered at the top of the project detail content
 * when the page is anchored to an *older* succeeded scan (`?scan=` set and not
 * equal to the latest succeeded id). It states which snapshot is being viewed
 * and offers a single "Back to latest" exit that clears the `?scan=` param.
 *
 * Pairs an icon with a label so the historical state is not color-only (CLAUDE.md
 * accessibility rule). Uses design tokens (`bg-risk-*`) — never hex.
 */

export interface SnapshotBannerProps {
  /**
   * Human-readable label for the pinned snapshot — the release name, or a
   * formatted date when the scan carried no release label. Already localized /
   * formatted by the caller.
   */
  label: string;
  /** Clears `?scan=` and returns the page to the latest snapshot. */
  onBackToLatest: () => void;
  className?: string;
}

export function SnapshotBanner({
  label,
  onBackToLatest,
  className,
}: SnapshotBannerProps) {
  const { t } = useTranslation("project_detail");
  return (
    <div
      data-testid="snapshot-banner"
      role="status"
      aria-live="polite"
      className={cn(
        "flex flex-wrap items-center justify-between gap-3 border-b border-risk-medium/40 bg-risk-medium/10 px-6 py-2.5 text-sm",
        className,
      )}
    >
      <div className="flex items-center gap-2">
        <History
          className="h-4 w-4 shrink-0 text-risk-medium"
          aria-hidden
        />
        <span className="font-medium text-foreground">
          {t("snapshot.banner", { label })}
        </span>
      </div>
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={onBackToLatest}
        data-testid="snapshot-exit"
      >
        {t("snapshot.back_to_latest")}
      </Button>
    </div>
  );
}
