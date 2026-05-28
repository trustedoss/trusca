import { ChevronDown, GitCompare, History, ShieldX } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuActiveCheck,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useReleases } from "@/features/projects/api/useReleases";
import type {
  ReleaseSeveritySummary,
  ReleaseSnapshot,
} from "@/features/projects/api/releasesApi";
import { releaseLabel } from "@/features/projects/lib/releaseLabel";
import { cn } from "@/lib/utils";

/**
 * ReleaseSwitcher — feature #28 Phase 1 (persistent version context).
 *
 * The Black-Duck-style "you're always inside a version, switch from anywhere"
 * control. Lives in the project-detail header next to the project name so it
 * reads as the page's version context rather than something buried in a tab.
 *
 * The trigger always reflects the CURRENTLY PINNED scan (read from `?scan=` by
 * the parent and matched against the releases list), so a hard reload restores
 * the correct label. The menu lists a top "Latest" item (clears `?scan=`) and
 * every release newest-first; selecting one sets `?scan=<scan_id>` — exactly
 * the same anchor the Releases tab "View snapshot" action uses, so the two
 * entry points stay consistent.
 *
 * CLAUDE.md "디자인 시스템": design tokens only (no hex), risk colors paired
 * with a label/icon (never color-only), inline (no modal). Built on the shadcn
 * DropdownMenu so keyboard navigation + focus management come for free.
 */

const PAGE_SIZE = 50;

/** Risk score → text token, mirroring RiskGauge / ReleasesTab thresholds. */
function riskDotClass(score: number | null): string {
  if (score == null) return "bg-muted-foreground/40";
  if (score >= 75) return "bg-risk-critical";
  if (score >= 50) return "bg-risk-high";
  if (score >= 25) return "bg-risk-medium";
  if (score > 0) return "bg-risk-low";
  return "bg-risk-info";
}

const SEVERITY_TOKEN: Record<keyof ReleaseSeveritySummary, string> = {
  critical: "text-risk-critical",
  high: "text-risk-high",
  medium: "text-risk-medium",
  low: "text-risk-low",
};

const SEVERITY_ORDER: Array<keyof ReleaseSeveritySummary> = [
  "critical",
  "high",
  "medium",
  "low",
];

export interface ReleaseSwitcherProps {
  projectId: string;
  /**
   * The currently pinned scan id (from `?scan=`), or `undefined` when the page
   * is showing the live latest view.
   */
  pinnedScanId: string | undefined;
  /** The latest succeeded scan id, or `null` when none / still resolving. */
  latestScanId: string | null;
  /** Whether the pinned scan is an older snapshot (read-only). */
  isHistorical: boolean;
  /** Pin a release snapshot — sets `?scan=<scanId>` (preserves other params). */
  onSelectRelease: (scanId: string) => void;
  /** Clear `?scan=` — returns to the live latest view. */
  onSelectLatest: () => void;
}

export function ReleaseSwitcher({
  projectId,
  pinnedScanId,
  latestScanId,
  isHistorical,
  onSelectRelease,
  onSelectLatest,
}: ReleaseSwitcherProps) {
  const { t, i18n } = useTranslation("project_detail");
  const locale = i18n.language;
  const navigate = useNavigate();

  const releases = useReleases(projectId, { page: 1, size: PAGE_SIZE });
  const items = releases.data?.items ?? [];

  // "Compare releases…" routes to the compare view with the same sensible
  // defaults as the Releases tab: target = newest, base = next down. Needs at
  // least two snapshots, so the item is disabled below two releases.
  const canCompare = items.length >= 2;
  function handleCompare() {
    if (!canCompare) return;
    const target = items[0].scan_id;
    const base = items[1].scan_id;
    navigate(
      `/projects/${projectId}/compare?base=${encodeURIComponent(
        base,
      )}&target=${encodeURIComponent(target)}`,
    );
  }

  // Resolve the snapshot the trigger should describe. When `?scan=` matches a
  // known release we render its label; the latest release supplies the date for
  // the live view. The active row is the pinned scan, or the latest when live.
  const latest = items.find((item) => item.scan_id === latestScanId) ?? null;
  const pinned = pinnedScanId
    ? items.find((item) => item.scan_id === pinnedScanId)
    : undefined;
  const activeScanId = pinnedScanId ?? latestScanId ?? null;

  // The project has no succeeded scan yet → no snapshot to switch to. Show a
  // disabled trigger so the control is discoverable without being misleading.
  const noReleases = !releases.isLoading && items.length === 0;

  const triggerLabel = (() => {
    if (releases.isLoading) return t("release_switcher.loading");
    if (noReleases) return t("release_switcher.none");
    if (isHistorical && pinned) {
      return t("release_switcher.trigger_historical", {
        label: releaseLabel(pinned, locale),
      });
    }
    if (latest) {
      return t("release_switcher.trigger_latest", {
        label: releaseLabel(latest, locale),
      });
    }
    // `?scan=` set but not yet resolved against the list, or latest unknown.
    return t("release_switcher.trigger_loading");
  })();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={noReleases || releases.isLoading}
          data-testid="release-switcher"
          data-historical={isHistorical ? "true" : "false"}
          data-pinned-scan-id={pinnedScanId ?? ""}
          className="h-7 gap-1.5 px-2 text-xs font-medium"
        >
          {isHistorical ? (
            <History className="h-3.5 w-3.5 text-risk-medium" aria-hidden />
          ) : null}
          <span className="max-w-[16rem] truncate" data-testid="release-switcher-label">
            {triggerLabel}
          </span>
          <ChevronDown className="h-3.5 w-3.5 opacity-60" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="max-h-[24rem] w-72 overflow-y-auto"
        data-testid="release-switcher-menu"
      >
        <DropdownMenuLabel>{t("release_switcher.menu_label")}</DropdownMenuLabel>

        <DropdownMenuItem
          onSelect={onSelectLatest}
          data-testid="release-switcher-latest"
          data-active={!isHistorical ? "true" : "false"}
        >
          <span className="flex flex-col">
            <span className="font-medium">{t("release_switcher.latest")}</span>
            <span className="text-xs text-muted-foreground">
              {t("release_switcher.latest_hint")}
            </span>
          </span>
          <DropdownMenuActiveCheck active={!isHistorical} />
        </DropdownMenuItem>

        {canCompare ? (
          <DropdownMenuItem
            onSelect={handleCompare}
            data-testid="release-switcher-compare"
          >
            <GitCompare className="h-3.5 w-3.5 shrink-0" aria-hidden />
            <span className="font-medium">{t("compare.switcher_item")}</span>
          </DropdownMenuItem>
        ) : null}

        <DropdownMenuSeparator />

        {releases.isLoading ? (
          <div
            className="px-2 py-1.5 text-xs text-muted-foreground"
            data-testid="release-switcher-loading"
          >
            {t("release_switcher.loading")}
          </div>
        ) : null}

        {!releases.isLoading && items.length === 0 ? (
          <div
            className="px-2 py-1.5 text-xs text-muted-foreground"
            data-testid="release-switcher-empty"
          >
            {t("release_switcher.none")}
          </div>
        ) : null}

        {items.map((item) => (
          <ReleaseMenuItem
            key={item.scan_id}
            release={item}
            locale={locale}
            isLatest={item.scan_id === latestScanId}
            active={item.scan_id === activeScanId}
            onSelect={() => onSelectRelease(item.scan_id)}
          />
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

interface ReleaseMenuItemProps {
  release: ReleaseSnapshot;
  locale: string;
  isLatest: boolean;
  active: boolean;
  onSelect: () => void;
}

function ReleaseMenuItem({
  release,
  locale,
  isLatest,
  active,
  onSelect,
}: ReleaseMenuItemProps) {
  const { t } = useTranslation("project_detail");
  const label = releaseLabel(release, locale);
  const nonZero = SEVERITY_ORDER.filter((key) => release.severity_summary[key] > 0);

  return (
    <DropdownMenuItem
      onSelect={onSelect}
      data-testid="release-switcher-item"
      data-scan-id={release.scan_id}
      data-active={active ? "true" : "false"}
      aria-label={t("release_switcher.item_aria", { label })}
    >
      <span
        className={cn("h-2 w-2 shrink-0 rounded-full", riskDotClass(release.risk_score))}
        aria-hidden
      />
      <span className="flex min-w-0 flex-col">
        <span className="flex items-center gap-1.5">
          <span className="truncate font-medium">{label}</span>
          {isLatest ? (
            <Badge
              variant="secondary"
              className="h-4 px-1 py-0 text-[10px] uppercase"
              data-testid="release-switcher-item-latest"
            >
              {t("release_switcher.latest_tag")}
            </Badge>
          ) : null}
        </span>
        <span className="flex items-center gap-2 text-xs text-muted-foreground">
          {release.gate_status === "fail" ? (
            <span
              className="inline-flex items-center gap-0.5 text-risk-critical"
              data-testid="release-switcher-item-gate-fail"
            >
              <ShieldX className="h-3 w-3" aria-hidden />
              {t("release_switcher.gate_fail")}
            </span>
          ) : null}
          {nonZero.length > 0 ? (
            <span className="flex items-center gap-1 font-mono tabular-nums">
              {nonZero.map((key) => (
                <span key={key} className={SEVERITY_TOKEN[key]}>
                  {t(`releases.severity_abbr.${key}`)}
                  {release.severity_summary[key]}
                </span>
              ))}
            </span>
          ) : (
            <span>{t("releases.severity_none")}</span>
          )}
        </span>
      </span>
      <DropdownMenuActiveCheck active={active} />
    </DropdownMenuItem>
  );
}
