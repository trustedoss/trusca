// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { Skeleton } from "@/components/ui/skeleton";
import {
  useProjectGovernance,
  type GovernanceTrendPoint,
} from "@/features/projects/api/governance";
import { cn } from "@/lib/utils";

/**
 * The governance band — what a scan result cannot tell you about itself.
 *
 * A scan report says what is in the code. This strip says what the
 * organisation has decided about it: whether the build gate would let it
 * ship, whether a known-exploited finding is past its deadline, whether
 * someone is waiting on an approval, and which way the last few scans moved.
 * Those are workflow facts, and a tool that runs once and prints has nowhere
 * to keep them.
 *
 * It sits between the header and the tabs deliberately. Everything here is
 * reachable from a tab already — the point is that reading it costs no
 * navigation, so the state of the project is present while you work inside
 * one of its tabs.
 *
 * The band is five tiles at desktop width and a two-column grid below that.
 * It is the densest strip in the product and W16's narrow-viewport gate
 * exists partly because of it: five tiles in a row is the shape that pushes
 * a phone into a sideways scroll.
 */

const SPARK_WIDTH = 96;
const SPARK_HEIGHT = 24;
const SPARK_PAD = 2;

function Tile({
  label,
  children,
  hint,
  to,
  tone = "neutral",
  testId,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
  to?: string;
  tone?: "neutral" | "warning" | "danger" | "ok";
  testId: string;
}) {
  const body = (
    <>
      <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <span className="flex items-baseline gap-2 text-lg font-semibold tabular-nums">
        {children}
      </span>
      {hint ? (
        <span className="truncate text-xs text-muted-foreground">{hint}</span>
      ) : null}
    </>
  );

  const className = cn(
    "flex min-w-0 flex-col gap-1 rounded-md border px-3 py-2",
    // Colour is the last signal, never the only one: each tile's number and
    // hint say the same thing in words.
    tone === "danger" && "border-status-danger-border bg-status-danger-subtle",
    tone === "warning" && "border-status-warning-border bg-status-warning-subtle",
    tone === "ok" && "border-status-success-border bg-status-success-subtle",
    to &&
      "transition-colors duration-fast ease-out-soft hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
  );

  return to ? (
    <Link to={to} data-testid={testId} data-tone={tone} className={className}>
      {body}
    </Link>
  ) : (
    <div data-testid={testId} data-tone={tone} className={className}>
      {body}
    </div>
  );
}

/** The last few scans' critical counts. Two points are not a trend. */
function TrendSpark({ points }: { points: GovernanceTrendPoint[] }) {
  if (points.length < 2) return null;

  const values = points.map((point) => point.critical);
  const max = Math.max(1, ...values);
  const step = SPARK_WIDTH / (points.length - 1);
  const usable = SPARK_HEIGHT - SPARK_PAD * 2;
  const path = values
    .map((value, index) => {
      const x = index * step;
      const y = SPARK_HEIGHT - SPARK_PAD - (value / max) * usable;
      return `${index === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg
      viewBox={`0 0 ${SPARK_WIDTH} ${SPARK_HEIGHT}`}
      preserveAspectRatio="none"
      className="h-6 w-full"
      aria-hidden
      data-testid="governance-trend-spark"
    >
      <path
        d={path}
        fill="none"
        stroke="var(--risk-critical)"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

/**
 * Why the gate failed, in the terms that actually failed it.
 *
 * The first version always printed both counts, so a deployment with
 * `GATE_EPSS_THRESHOLD` set showed "0 critical · 0 forbidden licences" beside
 * a blocked gate — every number in the sentence true, the sentence itself
 * useless. Only non-zero clauses appear, and the EPSS clause exists at all
 * because that condition can block on its own.
 */
function gateReason(
  gate: {
    critical_cve_count: number;
    forbidden_license_count: number;
    epss_gate_count: number;
    malicious_component_count: number;
  },
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  const clauses: string[] = [];
  if (gate.critical_cve_count > 0) {
    clauses.push(t("governance.gate_reason_critical", { count: gate.critical_cve_count }));
  }
  if (gate.forbidden_license_count > 0) {
    clauses.push(t("governance.gate_reason_licenses", { count: gate.forbidden_license_count }));
  }
  if (gate.malicious_component_count > 0) {
    // First clause, not last: this one says remove-and-rotate, and a reader
    // who stops after the first phrase should get that one.
    clauses.unshift(
      t("governance.gate_reason_malicious", {
        count: gate.malicious_component_count,
      }),
    );
  }
  if (gate.epss_gate_count > 0) {
    clauses.push(t("governance.gate_reason_epss", { count: gate.epss_gate_count }));
  }
  return clauses.join(" · ");
}

export function GovernanceBand({ projectId }: { projectId: string | null }) {
  const { t } = useTranslation("project_detail");
  const query = useProjectGovernance(projectId);

  if (query.isPending) {
    return (
      <div
        className="grid gap-2 px-6 pb-2 sm:grid-cols-2 xl:grid-cols-5"
        data-testid="governance-band-loading"
      >
        {[0, 1, 2, 3, 4].map((index) => (
          <Skeleton key={index} className="h-16" />
        ))}
      </div>
    );
  }

  // A band that failed to load must not render as a passing gate with no
  // overdue deadlines. It says nothing rather than something false.
  if (query.isError) {
    return (
      <p className="px-6 pb-2 text-xs text-muted-foreground" data-testid="governance-band-error">
        {t("governance.error")}
      </p>
    );
  }

  const band = query.data;
  const kevTotal = band.kev_sla.overdue + band.kev_sla.due_soon;
  const latest = band.trend.at(-1)?.critical ?? 0;
  const first = band.trend.at(0)?.critical ?? 0;
  const delta = latest - first;

  return (
    <div
      className="grid gap-2 px-6 pb-2 sm:grid-cols-2 xl:grid-cols-5"
      data-testid="governance-band"
      data-scanned={band.scanned ? "true" : "false"}
    >
      <Tile
        label={t("governance.risk")}
        testId="governance-risk"
        tone={band.scanned && band.risk_score >= 75 ? "danger" : "neutral"}
        hint={band.scanned ? undefined : t("governance.never_scanned")}
      >
        {band.scanned ? Math.round(band.risk_score) : "—"}
      </Tile>

      <Tile
        label={t("governance.gate")}
        testId="governance-gate"
        tone={
          band.gate.status === "fail"
            ? "danger"
            : band.gate.status === "pass"
              ? "ok"
              : "neutral"
        }
        hint={
          band.gate.status === "fail" ? gateReason(band.gate, t) : undefined
        }
        to={
          band.gate.status === "fail"
            ? `/projects/${band.project_id}?tab=vulnerabilities`
            : undefined
        }
      >
        {/* Null status is its own word. A gate that never ran and a gate that
         *  passed are the two states most costly to confuse here. */}
        {band.gate.status === null
          ? t("governance.gate_unknown")
          : t(`governance.gate_${band.gate.status}`)}
      </Tile>

      <Tile
        label={t("governance.kev")}
        testId="governance-kev"
        tone={band.kev_sla.overdue > 0 ? "danger" : kevTotal > 0 ? "warning" : "neutral"}
        hint={
          kevTotal > 0
            ? t("governance.kev_split", {
                overdue: band.kev_sla.overdue,
                dueSoon: band.kev_sla.due_soon,
              })
            : undefined
        }
        to={kevTotal > 0 ? `/projects/${band.project_id}?tab=vulnerabilities` : undefined}
      >
        {kevTotal}
      </Tile>

      {/* B2 - the tile counts this project's open approvals, so the link has
          to arrive at this project's queue. It used to land on the whole
          portfolio's, where the number the user just clicked was nowhere on
          the page. */}
      <Tile
        label={t("governance.approvals")}
        testId="governance-approvals"
        tone={band.pending_approvals > 0 ? "warning" : "neutral"}
        to={
          band.pending_approvals > 0
            ? `/approvals?project=${band.project_id}`
            : undefined
        }
      >
        {band.pending_approvals}
      </Tile>

      <div
        className="flex min-w-0 flex-col gap-1 rounded-md border px-3 py-2"
        data-testid="governance-trend"
        data-delta={delta}
      >
        <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          {t("governance.trend")}
        </span>
        {band.trend.length < 2 ? (
          <span className="text-xs text-muted-foreground">
            {t("governance.trend_needs_history")}
          </span>
        ) : (
          <>
            <span
              className={cn(
                "text-xs font-medium tabular-nums",
                delta > 0 && "text-status-danger-foreground",
                delta < 0 && "text-status-success-foreground",
                delta === 0 && "text-muted-foreground",
              )}
            >
              {t("governance.trend_delta", {
                delta: delta > 0 ? `+${delta}` : delta < 0 ? `−${Math.abs(delta)}` : "0",
                scans: band.trend.length,
              })}
            </span>
            <TrendSpark points={band.trend} />
          </>
        )}
      </div>
    </div>
  );
}
