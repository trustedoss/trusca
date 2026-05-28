import {
  ArrowLeft,
  ArrowLeftRight,
  ArrowRight,
  ChevronDown,
  Minus,
  ShieldCheck,
  ShieldX,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuActiveCheck,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  DiffComponent,
  DiffComponentChange,
  DiffGateStatus,
  DiffLicenseCategory,
  DiffSeverityBucket,
  DiffValuePair,
  DiffVulnerability,
  ProjectDiff,
} from "@/features/projects/api/diffApi";
import { useProjectDiff } from "@/features/projects/api/useProjectDiff";
import { useReleases } from "@/features/projects/api/useReleases";
import type { ReleaseSnapshot } from "@/features/projects/api/releasesApi";
import { projectErrorMessageKey } from "@/features/projects/lib/projectErrorMessage";
import { releaseLabel } from "@/features/projects/lib/releaseLabel";
import { SeverityBadge } from "@/features/projects/components/SeverityBadge";
import type { SeverityVariant } from "@/features/projects/components/SeverityBadge";
import { cn } from "@/lib/utils";

/**
 * ComparePage — feature #28 Phase 2 (release / version compare).
 *
 * Black-Duck-style "Compare to" screen rendered at
 * `/projects/:id/compare?base=<scan_id>&target=<scan_id>`. Two release
 * selectors (Base ◀ … ▶ Target) drive a single diff query; the result renders
 * as a summary delta strip + three stacked sections (Components /
 * Vulnerabilities / Licenses).
 *
 * CLAUDE.md "디자인 시스템": design tokens only (no hex), color never the only
 * signal (deltas pair with ↑/↓/+/− and labels), skeleton loading (no spinner),
 * inline states, compact density. The selected base/target live in the URL so
 * a hard reload restores the exact comparison.
 */

const PAGE_SIZE = 50;

const SEVERITY_ORDER: DiffSeverityBucket[] = ["critical", "high", "medium", "low"];
const LICENSE_ORDER: DiffLicenseCategory[] = [
  "prohibited",
  "conditional",
  "permissive",
  "unknown",
];

/** A higher value is "worse" for risk/severity/vuln counts — increase = red. */
const SEVERITY_TOKEN: Record<DiffSeverityBucket, string> = {
  critical: "text-risk-critical",
  high: "text-risk-high",
  medium: "text-risk-medium",
  low: "text-risk-low",
};

export function ComparePage() {
  const { t, i18n } = useTranslation("project_detail");
  const locale = i18n.language;
  const { id: projectId } = useParams<{ id: string }>();
  const [searchParams, setSearchParams] = useSearchParams();

  const baseParam = searchParams.get("base");
  const targetParam = searchParams.get("target");
  const base = baseParam && baseParam.length > 0 ? baseParam : undefined;
  const target = targetParam && targetParam.length > 0 ? targetParam : undefined;

  const releases = useReleases(projectId, { page: 1, size: PAGE_SIZE });
  const items = releases.data?.items ?? [];

  const diff = useProjectDiff(projectId, base, target);

  function setBase(scanId: string) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("base", scanId);
        return next;
      },
      { replace: true },
    );
  }

  function setTarget(scanId: string) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("target", scanId);
        return next;
      },
      { replace: true },
    );
  }

  function swap() {
    if (base == null || target == null) return;
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("base", target);
        next.set("target", base);
        return next;
      },
      { replace: true },
    );
  }

  if (!projectId) {
    return (
      <div className="p-6" data-testid="compare-missing-id">
        <Alert variant="destructive">
          <AlertDescription>{t("page.missing_id")}</AlertDescription>
        </Alert>
      </div>
    );
  }

  const bothSelected = base != null && target != null;

  return (
    <div
      className="flex min-h-screen flex-col bg-background text-foreground"
      data-testid="compare-page"
      data-project-id={projectId}
      data-base={base ?? ""}
      data-target={target ?? ""}
    >
      <header className="flex flex-col gap-2 border-b px-6 py-3">
        <nav
          className="flex items-center gap-2 text-xs text-muted-foreground"
          aria-label={t("page.breadcrumb_aria")}
        >
          <Link
            to="/projects"
            className="transition-colors duration-fast ease-out-soft hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            data-testid="compare-breadcrumb-projects"
          >
            {t("page.breadcrumb_projects")}
          </Link>
          <span aria-hidden>/</span>
          <Link
            to={`/projects/${projectId}`}
            className="transition-colors duration-fast ease-out-soft hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            data-testid="compare-breadcrumb-project"
          >
            {t("compare.back_to_project")}
          </Link>
          <span aria-hidden>/</span>
          <span data-testid="compare-breadcrumb-current">
            {t("compare.breadcrumb_compare")}
          </span>
        </nav>
        <h1
          className="text-lg font-semibold tracking-tight"
          data-testid="compare-title"
        >
          {t("compare.title")}
        </h1>
        <p className="text-xs text-muted-foreground">{t("compare.subtitle")}</p>
      </header>

      <div className="flex flex-col gap-6 p-6">
        {/* Base ◀ … ▶ Target selectors */}
        <div
          className="flex flex-wrap items-end gap-3"
          data-testid="compare-selectors"
        >
          <ReleaseSelector
            kind="base"
            projectId={projectId}
            items={items}
            isLoading={releases.isLoading}
            selectedScanId={base}
            locale={locale}
            onSelect={setBase}
          />
          <Button
            type="button"
            size="icon"
            variant="outline"
            className="mb-0.5 h-8 w-8 shrink-0"
            onClick={swap}
            disabled={!bothSelected}
            aria-label={t("compare.selector.swap")}
            title={t("compare.selector.swap")}
            data-testid="compare-swap"
          >
            <ArrowLeftRight className="h-4 w-4" aria-hidden />
          </Button>
          <ReleaseSelector
            kind="target"
            projectId={projectId}
            items={items}
            isLoading={releases.isLoading}
            selectedScanId={target}
            locale={locale}
            onSelect={setTarget}
          />
        </div>

        {!bothSelected ? (
          <div
            className="rounded-md border border-dashed p-8 text-center"
            data-testid="compare-missing-params"
          >
            <p className="text-sm font-medium">
              {t("compare.missing_params.title")}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {t("compare.missing_params.description")}
            </p>
          </div>
        ) : null}

        {bothSelected && diff.isError ? (
          <Alert variant="destructive" data-testid="compare-error">
            <AlertDescription>
              <div className="font-semibold">{t("compare.errors.title")}</div>
              <div className="text-sm">
                {t(projectErrorMessageKey(diff.error, "compare.errors"), {
                  defaultValue: t("compare.errors.unknown"),
                })}
              </div>
            </AlertDescription>
          </Alert>
        ) : null}

        {bothSelected && diff.isLoading ? (
          <div
            className="flex flex-col gap-4"
            data-testid="compare-loading"
            aria-label={t("compare.loading_aria")}
          >
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-24 w-full" />
              ))}
            </div>
            <Skeleton className="h-48 w-full" />
            <Skeleton className="h-48 w-full" />
          </div>
        ) : null}

        {bothSelected && !diff.isLoading && !diff.isError && diff.data ? (
          <CompareBody diff={diff.data} />
        ) : null}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Release selector dropdown (Base / Target)
// ---------------------------------------------------------------------------

interface ReleaseSelectorProps {
  kind: "base" | "target";
  projectId: string;
  items: ReleaseSnapshot[];
  isLoading: boolean;
  selectedScanId: string | undefined;
  locale: string;
  onSelect: (scanId: string) => void;
}

function ReleaseSelector({
  kind,
  items,
  isLoading,
  selectedScanId,
  locale,
  onSelect,
}: ReleaseSelectorProps) {
  const { t } = useTranslation("project_detail");
  const selected = selectedScanId
    ? items.find((item) => item.scan_id === selectedScanId)
    : undefined;
  const triggerLabel = (() => {
    if (isLoading) return t("compare.selector.loading");
    if (selected) return releaseLabel(selected, locale);
    if (items.length === 0) return t("compare.selector.none");
    return t("compare.selector.menu_label");
  })();

  const labelKey =
    kind === "base"
      ? "compare.selector.base_label"
      : "compare.selector.target_label";
  const ariaKey =
    kind === "base"
      ? "compare.selector.base_aria"
      : "compare.selector.target_aria";

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1">
      <span className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {kind === "base" ? (
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
        ) : null}
        {t(labelKey)}
        {kind === "target" ? (
          <ArrowRight className="h-3.5 w-3.5" aria-hidden />
        ) : null}
      </span>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="outline"
            disabled={isLoading || items.length === 0}
            aria-label={t(ariaKey)}
            data-testid={`compare-selector-${kind}`}
            data-scan-id={selectedScanId ?? ""}
            className="h-8 w-full justify-between gap-2 px-3 text-sm font-medium"
          >
            <span className="truncate" data-testid={`compare-selector-${kind}-label`}>
              {triggerLabel}
            </span>
            <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-60" aria-hidden />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="start"
          className="max-h-[24rem] w-72 overflow-y-auto"
          data-testid={`compare-selector-${kind}-menu`}
        >
          <DropdownMenuLabel>
            {t("compare.selector.menu_label")}
          </DropdownMenuLabel>
          {items.map((item) => {
            const active = item.scan_id === selectedScanId;
            return (
              <DropdownMenuItem
                key={item.scan_id}
                onSelect={() => onSelect(item.scan_id)}
                data-testid={`compare-selector-${kind}-item`}
                data-scan-id={item.scan_id}
                data-active={active ? "true" : "false"}
              >
                <span className="truncate">{releaseLabel(item, locale)}</span>
                <DropdownMenuActiveCheck active={active} />
              </DropdownMenuItem>
            );
          })}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Body — summary strip + sections (rendered only when diff data is present)
// ---------------------------------------------------------------------------

function CompareBody({ diff }: { diff: ProjectDiff }) {
  const { t } = useTranslation("project_detail");

  const noDifferences =
    diff.components.added.length === 0 &&
    diff.components.removed.length === 0 &&
    diff.components.changed.length === 0 &&
    diff.vulnerabilities.introduced.length === 0 &&
    diff.vulnerabilities.resolved.length === 0;

  return (
    <>
      {diff.truncated ? (
        <p
          className="text-xs text-muted-foreground"
          data-testid="compare-truncated"
        >
          {t("compare.truncated")}
        </p>
      ) : null}

      <SummaryStrip diff={diff} />

      {noDifferences ? (
        <div
          className="rounded-md border border-dashed p-8 text-center"
          data-testid="compare-empty"
        >
          <p className="text-sm font-medium">{t("compare.empty.title")}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("compare.empty.description")}
          </p>
        </div>
      ) : (
        <>
          <ComponentsSection diff={diff} />
          <VulnerabilitiesSection diff={diff} />
        </>
      )}

      <LicensesSection diff={diff} />
    </>
  );
}

// ---------------------------------------------------------------------------
// Summary delta strip
// ---------------------------------------------------------------------------

function SummaryStrip({ diff }: { diff: ProjectDiff }) {
  const { t } = useTranslation("project_detail");
  const { summary } = diff;

  return (
    <div
      className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4"
      data-testid="compare-summary"
    >
      <Card data-testid="compare-summary-risk">
        <CardHeader className="p-4 pb-2">
          <CardTitle className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {t("compare.summary.risk_score")}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-4 pt-0">
          <NumberDelta
            pair={summary.risk_score}
            higherIsWorse
            testid="compare-risk-delta"
            format={(n) => n.toFixed(0)}
          />
        </CardContent>
      </Card>

      <Card data-testid="compare-summary-severity">
        <CardHeader className="p-4 pb-2">
          <CardTitle className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {t("compare.summary.severity")}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-1.5 p-4 pt-0">
          {SEVERITY_ORDER.map((bucket) => (
            <div
              key={bucket}
              className="flex items-center justify-between gap-2"
              data-testid={`compare-severity-${bucket}`}
            >
              <span
                className={cn(
                  "text-xs font-medium",
                  SEVERITY_TOKEN[bucket],
                )}
              >
                {t(`severity.${bucket}`)}
              </span>
              <NumberDelta
                pair={summary.severity[bucket]}
                higherIsWorse
                compact
                testid={`compare-severity-${bucket}-delta`}
                format={(n) => String(n)}
              />
            </div>
          ))}
        </CardContent>
      </Card>

      <Card data-testid="compare-summary-gate">
        <CardHeader className="p-4 pb-2">
          <CardTitle className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {t("compare.summary.gate")}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-4 pt-0">
          <GateDelta pair={summary.gate} />
        </CardContent>
      </Card>

      <Card data-testid="compare-summary-components">
        <CardHeader className="p-4 pb-2">
          <CardTitle className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {t("compare.summary.component_count")}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-4 pt-0">
          {/* More components is neutral, not "worse" — no red/green tint. */}
          <NumberDelta
            pair={summary.component_count}
            testid="compare-components-delta"
            format={(n) => String(n)}
          />
        </CardContent>
      </Card>
    </div>
  );
}

interface NumberDeltaProps {
  pair: DiffValuePair<number | null>;
  /** When set, an increase tints red (worse) and a decrease green (better). */
  higherIsWorse?: boolean;
  compact?: boolean;
  testid: string;
  format: (n: number) => string;
}

/**
 * Renders `base → target` with a signed delta. Color tints the delta only when
 * `higherIsWorse` is set, and is always paired with an arrow + sign so color is
 * never the only signal. A null endpoint renders the em-dash placeholder.
 */
function NumberDelta({
  pair,
  higherIsWorse,
  compact,
  testid,
  format,
}: NumberDeltaProps) {
  const { t } = useTranslation("project_detail");
  const baseText = pair.base != null ? format(pair.base) : t("compare.summary.no_value");
  const targetText =
    pair.target != null ? format(pair.target) : t("compare.summary.no_value");

  // A delta is only meaningful when both endpoints are numeric.
  const delta =
    pair.base != null && pair.target != null ? pair.target - pair.base : null;

  let tone = "text-muted-foreground";
  let aria: string | undefined;
  if (delta != null && delta !== 0 && higherIsWorse) {
    if (delta > 0) {
      tone = "text-risk-critical";
      aria = t("compare.summary.delta_increase_aria", { delta: Math.abs(delta) });
    } else {
      tone = "text-emerald-600";
      aria = t("compare.summary.delta_decrease_aria", { delta: Math.abs(delta) });
    }
  }

  return (
    <div
      className="flex items-baseline gap-2 font-mono tabular-nums"
      data-testid={testid}
      data-delta={delta ?? ""}
    >
      <span className={cn("text-muted-foreground", compact ? "text-sm" : "text-lg")}>
        {baseText}
      </span>
      <ArrowRight
        className="h-3.5 w-3.5 shrink-0 self-center text-muted-foreground"
        aria-hidden
      />
      <span className={cn("font-semibold", compact ? "text-sm" : "text-lg")}>
        {targetText}
      </span>
      {delta != null && delta !== 0 ? (
        <span
          className={cn("flex items-center gap-0.5 text-xs font-medium", tone)}
          data-testid={`${testid}-direction`}
          aria-label={aria}
        >
          {delta > 0 ? (
            <TrendingUp className="h-3 w-3" aria-hidden />
          ) : (
            <TrendingDown className="h-3 w-3" aria-hidden />
          )}
          {delta > 0 ? "+" : "−"}
          {Math.abs(delta)}
        </span>
      ) : (
        <span
          className="flex items-center gap-0.5 text-xs text-muted-foreground"
          data-testid={`${testid}-direction`}
        >
          <Minus className="h-3 w-3" aria-hidden />
        </span>
      )}
    </div>
  );
}

function GateDelta({ pair }: { pair: DiffValuePair<DiffGateStatus | null> }) {
  const { t } = useTranslation("project_detail");

  // pass→fail is bad (red), fail→pass is good (green); same value = neutral.
  const worsened = pair.base === "pass" && pair.target === "fail";
  const improved = pair.base === "fail" && pair.target === "pass";

  return (
    <div
      className="flex items-center gap-2"
      data-testid="compare-gate-delta"
      data-base={pair.base ?? "none"}
      data-target={pair.target ?? "none"}
    >
      <GatePill status={pair.base} side="base" />
      <ArrowRight
        className={cn(
          "h-3.5 w-3.5 shrink-0",
          worsened
            ? "text-risk-critical"
            : improved
              ? "text-emerald-600"
              : "text-muted-foreground",
        )}
        aria-label={
          worsened || improved
            ? t("compare.summary.gate_change_aria", {
                base: t(gateLabelKey(pair.base)),
                target: t(gateLabelKey(pair.target)),
              })
            : undefined
        }
      />
      <GatePill status={pair.target} side="target" />
    </div>
  );
}

function gateLabelKey(status: DiffGateStatus | null): string {
  if (status === "pass") return "compare.summary.gate_pass";
  if (status === "fail") return "compare.summary.gate_fail";
  return "compare.summary.gate_none";
}

function GatePill({
  status,
  side,
}: {
  status: DiffGateStatus | null;
  side: "base" | "target";
}) {
  const { t } = useTranslation("project_detail");
  if (status == null) {
    return (
      <Badge tone="info" data-testid={`compare-gate-${side}`} data-gate="none">
        {t("compare.summary.gate_none")}
      </Badge>
    );
  }
  if (status === "pass") {
    return (
      <Badge
        tone="success"
        className="gap-1.5"
        data-testid={`compare-gate-${side}`}
        data-gate="pass"
      >
        <ShieldCheck className="h-3.5 w-3.5" aria-hidden />
        {t("compare.summary.gate_pass")}
      </Badge>
    );
  }
  return (
    <Badge
      variant="destructive"
      className="gap-1.5"
      data-testid={`compare-gate-${side}`}
      data-gate="fail"
    >
      <ShieldX className="h-3.5 w-3.5" aria-hidden />
      {t("compare.summary.gate_fail")}
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// Components section
// ---------------------------------------------------------------------------

function ComponentsSection({ diff }: { diff: ProjectDiff }) {
  const { t } = useTranslation("project_detail");
  const { removed, added, changed } = diff.components;

  return (
    <Card data-testid="compare-components">
      <CardHeader>
        <CardTitle className="text-base">{t("compare.components.title")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        <ComponentGroup
          testid="compare-components-removed"
          tone="removed"
          header={t("compare.components.removed_header", { count: removed.length })}
          empty={t("compare.components.removed_empty")}
          items={removed}
          renderItem={(c) => <RemovedAddedRow component={c} sign="−" />}
          keyOf={(c) => c.purl}
        />
        <ComponentGroup
          testid="compare-components-added"
          tone="added"
          header={t("compare.components.added_header", { count: added.length })}
          empty={t("compare.components.added_empty")}
          items={added}
          renderItem={(c) => <RemovedAddedRow component={c} sign="+" />}
          keyOf={(c) => c.purl}
        />
        <ComponentGroup
          testid="compare-components-changed"
          tone="changed"
          header={t("compare.components.changed_header", { count: changed.length })}
          empty={t("compare.components.changed_empty")}
          items={changed}
          renderItem={(c) => <ChangedRow change={c} />}
          keyOf={(c) => c.purl}
        />
      </CardContent>
    </Card>
  );
}

interface ComponentGroupProps<T> {
  testid: string;
  tone: "removed" | "added" | "changed";
  header: string;
  empty: string;
  items: T[];
  renderItem: (item: T) => ReactNode;
  keyOf: (item: T) => string;
}

const GROUP_HEADER_TONE: Record<"removed" | "added" | "changed", string> = {
  removed: "text-risk-critical",
  added: "text-emerald-600",
  changed: "text-risk-medium",
};

function ComponentGroup<T>({
  testid,
  tone,
  header,
  empty,
  items,
  renderItem,
  keyOf,
}: ComponentGroupProps<T>) {
  return (
    <div data-testid={testid} data-count={items.length}>
      <h3 className={cn("text-sm font-semibold", GROUP_HEADER_TONE[tone])}>
        {header}
      </h3>
      {items.length === 0 ? (
        <p className="mt-1 text-xs text-muted-foreground" data-testid={`${testid}-empty`}>
          {empty}
        </p>
      ) : (
        <ul className="mt-2 divide-y rounded-md border">
          {items.map((item) => (
            <li
              key={keyOf(item)}
              className="flex items-center gap-2 px-3 py-2"
              style={{ minHeight: "var(--table-row)" }}
              data-testid={`${testid}-row`}
            >
              {renderItem(item)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function componentDisplayName(c: { name: string; namespace: string | null }): string {
  return c.namespace ? `${c.namespace}/${c.name}` : c.name;
}

function RemovedAddedRow({
  component,
  sign,
}: {
  component: DiffComponent;
  sign: "+" | "−";
}) {
  return (
    <>
      <span
        aria-hidden
        className={cn(
          "shrink-0 font-mono text-sm font-bold",
          sign === "+" ? "text-emerald-600" : "text-risk-critical",
        )}
      >
        {sign}
      </span>
      <span className="min-w-0 flex-1 truncate text-sm font-medium">
        {componentDisplayName(component)}
      </span>
      <span className="shrink-0 font-mono text-xs text-muted-foreground">
        {component.version}
      </span>
    </>
  );
}

function ChangedRow({ change }: { change: DiffComponentChange }) {
  const { t } = useTranslation("project_detail");
  return (
    <>
      <span aria-hidden className="shrink-0 font-mono text-sm font-bold text-risk-medium">
        ~
      </span>
      <span className="min-w-0 flex-1 truncate text-sm font-medium">
        {componentDisplayName(change)}
      </span>
      <span className="shrink-0 font-mono text-xs">
        {t("compare.components.version_change", {
          base: change.base_version,
          target: change.target_version,
        })}
      </span>
    </>
  );
}

// ---------------------------------------------------------------------------
// Vulnerabilities section
// ---------------------------------------------------------------------------

function VulnerabilitiesSection({ diff }: { diff: ProjectDiff }) {
  const { t } = useTranslation("project_detail");
  const { resolved, introduced } = diff.vulnerabilities;

  return (
    <Card data-testid="compare-vulnerabilities">
      <CardHeader>
        <CardTitle className="text-base">
          {t("compare.vulnerabilities.title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        <VulnGroup
          testid="compare-vulns-resolved"
          tone="added"
          header={t("compare.vulnerabilities.resolved_header", {
            count: resolved.length,
          })}
          empty={t("compare.vulnerabilities.resolved_empty")}
          items={resolved}
          sign="−"
        />
        <VulnGroup
          testid="compare-vulns-introduced"
          tone="removed"
          header={t("compare.vulnerabilities.introduced_header", {
            count: introduced.length,
          })}
          empty={t("compare.vulnerabilities.introduced_empty")}
          items={introduced}
          sign="+"
        />
      </CardContent>
    </Card>
  );
}

interface VulnGroupProps {
  testid: string;
  tone: "removed" | "added";
  header: string;
  empty: string;
  items: DiffVulnerability[];
  sign: "+" | "−";
}

function VulnGroup({ testid, tone, header, empty, items, sign }: VulnGroupProps) {
  const { t } = useTranslation("project_detail");
  return (
    <div data-testid={testid} data-count={items.length}>
      <h3
        className={cn(
          "text-sm font-semibold",
          tone === "added" ? "text-emerald-600" : "text-risk-critical",
        )}
      >
        {header}
      </h3>
      {items.length === 0 ? (
        <p className="mt-1 text-xs text-muted-foreground" data-testid={`${testid}-empty`}>
          {empty}
        </p>
      ) : (
        <ul className="mt-2 divide-y rounded-md border">
          {items.map((v) => (
            <li
              key={`${v.cve_id}:${v.component_name}@${v.component_version}`}
              className="flex items-center gap-2 px-3 py-2"
              style={{ minHeight: "var(--table-row)" }}
              data-testid={`${testid}-row`}
            >
              <span
                aria-hidden
                className={cn(
                  "shrink-0 font-mono text-sm font-bold",
                  sign === "+" ? "text-risk-critical" : "text-emerald-600",
                )}
              >
                {sign}
              </span>
              <span className="shrink-0 font-mono text-sm font-medium">
                {v.cve_id}
              </span>
              <SeverityBadge severity={normalizeSeverity(v.severity)} />
              <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground">
                {t("compare.vulnerabilities.at_component", {
                  name: v.component_name,
                  version: v.component_version,
                })}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Map an arbitrary backend severity string onto the SeverityBadge variants. */
function normalizeSeverity(severity: string): SeverityVariant {
  const s = severity.toLowerCase();
  if (
    s === "critical" ||
    s === "high" ||
    s === "medium" ||
    s === "low" ||
    s === "info" ||
    s === "none" ||
    s === "unknown"
  ) {
    return s;
  }
  return "unknown";
}

// ---------------------------------------------------------------------------
// Licenses section
// ---------------------------------------------------------------------------

function LicensesSection({ diff }: { diff: ProjectDiff }) {
  const { t } = useTranslation("project_detail");
  const delta = diff.licenses.category_delta;

  return (
    <Card data-testid="compare-licenses">
      <CardHeader>
        <CardTitle className="text-base">{t("compare.licenses.title")}</CardTitle>
      </CardHeader>
      <CardContent>
        <table className="w-full text-sm" data-testid="compare-licenses-table">
          <thead className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="px-3 py-2 font-medium">
                {t("compare.licenses.col_category")}
              </th>
              <th className="px-3 py-2 text-right font-medium">
                {t("compare.licenses.col_base")}
              </th>
              <th className="px-3 py-2 text-right font-medium">
                {t("compare.licenses.col_target")}
              </th>
            </tr>
          </thead>
          <tbody>
            {LICENSE_ORDER.map((category) => {
              const pair = delta[category];
              // "Introduced" = a risk category that target has but base did not
              // (only meaningful for the two risk-bearing buckets).
              const introduced =
                (category === "prohibited" || category === "conditional") &&
                pair.base === 0 &&
                pair.target > 0;
              return (
                <tr
                  key={category}
                  className="border-b last:border-b-0"
                  style={{ height: "var(--table-row)" }}
                  data-testid={`compare-license-${category}`}
                  data-introduced={introduced ? "true" : "false"}
                >
                  <td className="px-3 py-2">
                    <span className="flex items-center gap-2">
                      <span className="font-medium">
                        {t(`compare.licenses.category.${category}`)}
                      </span>
                      {introduced ? (
                        <Badge
                          variant="destructive"
                          data-testid={`compare-license-${category}-introduced`}
                          aria-label={t("compare.licenses.introduced_aria", {
                            category: t(`compare.licenses.category.${category}`),
                          })}
                        >
                          {t("compare.licenses.introduced_badge")}
                        </Badge>
                      ) : null}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums text-muted-foreground">
                    {pair.base}
                  </td>
                  <td
                    className={cn(
                      "px-3 py-2 text-right font-mono font-semibold tabular-nums",
                      introduced && "text-risk-critical",
                    )}
                  >
                    {pair.target}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}
