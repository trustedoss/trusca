/**
 * SbomConformancePanel — feat/model3-conformance-panel.
 *
 * Renders the received-SBOM conformance verdict (model 3 external ingest). Pure
 * presentational component: the parent fetches via `useSbomConformance` and
 * passes the `SbomConformanceRead` down. Layout:
 *
 *   ┌─────────────────────────────────────────────────────────┐
 *   │ SBOM conformance            [● Pass]   (data-result)     │
 *   │ Format: CycloneDX · Components: 412                      │
 *   │ PURL 96% · License 88% · Hash —                          │
 *   ├─────────────────────────────────────────────────────────┤
 *   │ Check            Status   Detail / missing                │
 *   │ Timestamp        [● Pass] …                               │
 *   │ PURL coverage    [● Warn] 8 components missing purl       │
 *   │                          pkg:a pkg:b pkg:c … +5 more      │
 *   └─────────────────────────────────────────────────────────┘
 *
 * Accessibility: every result/status badge pairs a tinted dot with a localized
 * text label so color is never the only signal (CLAUDE.md "디자인 시스템" +
 * WCAG). Check labels prefer the localized `conformance.check_id.{id}` string
 * and fall back to the backend-supplied `check.label` for any id the FE mirror
 * hasn't learned yet (forward-compat — the catalog-mirror contract test keeps
 * the canonical 9 in lock-step).
 */
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import type {
  SbomCheckStatus,
  SbomConformanceCheck,
  SbomConformanceRead,
  SbomConformanceResult,
} from "@/lib/projectsApi";
import { cn } from "@/lib/utils";

/** Max number of `missing` entries rendered before collapsing to "+N more". */
const MISSING_VISIBLE_LIMIT = 5;

type Tone = "success" | "medium" | "critical";

const RESULT_TONE: Record<SbomConformanceResult, Tone> = {
  pass: "success",
  warn: "medium",
  fail: "critical",
};

const RESULT_DOT: Record<SbomConformanceResult, string> = {
  pass: "bg-emerald-500",
  warn: "bg-risk-medium",
  fail: "bg-risk-critical",
};

const CHECK_TONE: Record<SbomCheckStatus, Tone> = {
  pass: "success",
  warn: "medium",
  fail: "critical",
};

const CHECK_DOT: Record<SbomCheckStatus, string> = {
  pass: "bg-emerald-500",
  warn: "bg-risk-medium",
  fail: "bg-risk-critical",
};

export interface SbomConformancePanelProps {
  conformance: SbomConformanceRead;
}

function formatPct(value: number | null): string {
  return value == null ? "—" : `${value}%`;
}

export function SbomConformancePanel({
  conformance,
}: SbomConformancePanelProps) {
  const { t } = useTranslation("scans");

  return (
    <section
      className="rounded-md border bg-card p-4 shadow-sm"
      data-testid="conformance-panel"
    >
      <header className="flex flex-wrap items-center gap-3">
        <h2 className="text-sm font-semibold tracking-tight">
          {t("conformance.title")}
        </h2>
        <ResultBadge result={conformance.result} />
      </header>

      <dl
        className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-xs sm:grid-cols-3"
        data-testid="conformance-summary"
      >
        <SummaryItem
          label={t("conformance.label.source_format")}
          value={conformance.source_format}
          testId="conformance-source-format"
        />
        <SummaryItem
          label={t("conformance.label.component_count")}
          value={String(conformance.component_count)}
          testId="conformance-component-count"
        />
        <SummaryItem
          label={t("conformance.label.purl_coverage")}
          value={formatPct(conformance.purl_coverage_pct)}
          testId="conformance-purl-coverage"
        />
        <SummaryItem
          label={t("conformance.label.license_coverage")}
          value={formatPct(conformance.license_coverage_pct)}
          testId="conformance-license-coverage"
        />
        <SummaryItem
          label={t("conformance.label.hash_coverage")}
          value={formatPct(conformance.hash_coverage_pct)}
          testId="conformance-hash-coverage"
        />
      </dl>

      <div
        className="mt-4 grid grid-cols-1 divide-y rounded-md border"
        data-testid="conformance-checks-table"
        role="table"
        aria-label={t("conformance.title")}
      >
        {conformance.checks.map((check) => (
          <CheckRow key={check.id} check={check} />
        ))}
      </div>
    </section>
  );
}

interface SummaryItemProps {
  label: string;
  value: string;
  testId: string;
}

function SummaryItem({ label, value, testId }: SummaryItemProps) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">
        {label}
      </dt>
      <dd className="font-mono text-sm" data-testid={testId}>
        {value}
      </dd>
    </div>
  );
}

function ResultBadge({ result }: { result: SbomConformanceResult }) {
  const { t } = useTranslation("scans");
  return (
    <Badge
      tone={RESULT_TONE[result]}
      data-testid="conformance-badge"
      data-result={result}
      className="gap-1.5"
    >
      <span
        aria-hidden
        className={cn(
          "inline-block h-1.5 w-1.5 rounded-full",
          RESULT_DOT[result],
        )}
      />
      <span>{t(`conformance.result.${result}`)}</span>
    </Badge>
  );
}

function CheckStatusBadge({ status }: { status: SbomCheckStatus }) {
  const { t } = useTranslation("scans");
  return (
    <Badge
      tone={CHECK_TONE[status]}
      data-testid="conformance-check-status"
      data-status={status}
      className="gap-1.5"
    >
      <span
        aria-hidden
        className={cn(
          "inline-block h-1.5 w-1.5 rounded-full",
          CHECK_DOT[status],
        )}
      />
      <span>{t(`conformance.check_status.${status}`)}</span>
    </Badge>
  );
}

function CheckRow({ check }: { check: SbomConformanceCheck }) {
  const { t } = useTranslation("scans");
  // Prefer the localized canonical label; fall back to the backend's label for
  // any id the FE mirror hasn't enumerated yet (forward-compat).
  const localized = t(`conformance.check_id.${check.id}`, {
    defaultValue: "",
  });
  const label = localized || check.label;

  const visible = check.missing.slice(0, MISSING_VISIBLE_LIMIT);
  const overflow = check.missing.length - visible.length;

  return (
    <div
      className="flex flex-col gap-1 px-3 py-2 sm:flex-row sm:items-start sm:gap-3"
      data-testid={`check-${check.id}`}
      data-required={check.required ? "true" : "false"}
      role="row"
    >
      <div className="flex min-w-0 flex-col gap-0.5 sm:w-48 sm:shrink-0">
        <span className="text-sm font-medium">{label}</span>
        <span className="text-[11px] uppercase tracking-wide text-muted-foreground">
          {check.required
            ? t("conformance.label.required")
            : t("conformance.label.recommended")}
        </span>
      </div>

      <div className="sm:shrink-0">
        <CheckStatusBadge status={check.status} />
      </div>

      <div className="min-w-0 flex-1">
        {check.detail ? (
          <p className="text-xs text-muted-foreground">{check.detail}</p>
        ) : null}
        {check.missing.length > 0 ? (
          <ul
            className="mt-1 flex flex-wrap gap-1"
            data-testid={`check-${check.id}-missing`}
          >
            {visible.map((item) => (
              <li
                key={item}
                className="inline-flex max-w-full items-center truncate rounded-sm border border-border bg-muted px-1.5 py-0.5 font-mono text-[11px] text-foreground"
              >
                {item}
              </li>
            ))}
            {overflow > 0 ? (
              <li
                className="inline-flex items-center rounded-sm border border-border bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground"
                data-testid={`check-${check.id}-missing-more`}
              >
                {t("conformance.missing_more", { count: overflow })}
              </li>
            ) : null}
          </ul>
        ) : null}
      </div>
    </div>
  );
}
