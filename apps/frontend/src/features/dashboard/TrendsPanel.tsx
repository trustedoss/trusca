// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  TREND_WINDOWS,
  useDashboardTrends,
  type DashboardTrends,
  type TrendPoint,
  type TrendWindow,
} from "@/features/dashboard/api/trends";
import {
  formatNumber,
  formatSignedDelta,
  resolveLocale,
} from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * Risk over time — the second thing a portal can show that a single scan
 * cannot.
 *
 * The action queue says what is waiting; this says which direction the
 * portfolio is moving, which is the question a report printed once can never
 * answer. It needs history, and history needs a database that outlives the
 * scan.
 *
 * Three panels rather than one chart, deliberately. Standing exposure
 * (critical, KEV) and daily movement (new, resolved) are different measures
 * on different scales, and putting them on one plot means two y-axes — the
 * single most reliable way to make a chart lie. Small multiples share the
 * time axis and nothing else.
 *
 * Levels are carried forward on days nobody scanned, so the line is flat
 * through a quiet week rather than dropping to zero. `scan_count` is what
 * tells the two apart, and the footnote says so in words: a flat line can
 * mean "nothing changed" or "nobody looked", and the user is entitled to
 * know which.
 */

const SPARK_WIDTH = 120;
const SPARK_HEIGHT = 36;
/** Half the stroke width, so a line at the extreme of the range is not clipped. */
const SPARK_PAD = 2;

type LevelKey = "critical_open" | "kev_open";

/** `(x, y)` in viewBox units for each point of a level series. */
function levelPoints(points: TrendPoint[], key: LevelKey): Array<[number, number]> {
  const values = points.map((point) => point[key]);
  const max = Math.max(1, ...values);
  const step = points.length > 1 ? SPARK_WIDTH / (points.length - 1) : 0;
  const usable = SPARK_HEIGHT - SPARK_PAD * 2;
  return values.map((value, index) => [
    points.length > 1 ? index * step : SPARK_WIDTH / 2,
    SPARK_HEIGHT - SPARK_PAD - (value / max) * usable,
  ]);
}

interface LevelSparklineProps {
  points: TrendPoint[];
  seriesKey: LevelKey;
  /** CSS custom property for the line. */
  stroke: string;
  /** CSS custom property for the area beneath it. */
  fill: string;
  label: string;
}

/**
 * One level series as a filled sparkline.
 *
 * Each panel carries a single series, so identity never rests on telling two
 * hues apart — the heading names the series and the colour is decoration.
 * `vector-effect="non-scaling-stroke"` keeps the line 2px at any rendered
 * width, which a plain viewBox scale would not.
 */
function LevelSparkline({
  points,
  seriesKey,
  stroke,
  fill,
  label,
}: LevelSparklineProps) {
  if (points.length === 0) return null;

  const coords = levelPoints(points, seriesKey);
  const line = coords
    .map(([x, y], index) => `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`)
    .join(" ");
  const area = `${line} L ${SPARK_WIDTH} ${SPARK_HEIGHT} L 0 ${SPARK_HEIGHT} Z`;

  return (
    <svg
      viewBox={`0 0 ${SPARK_WIDTH} ${SPARK_HEIGHT}`}
      preserveAspectRatio="none"
      className="h-9 w-full"
      role="img"
      aria-label={label}
      data-testid={`trend-spark-${seriesKey}`}
    >
      <path d={area} fill={fill} fillOpacity={0.14} />
      <path
        d={line}
        fill="none"
        stroke={stroke}
        strokeWidth={2}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

interface LevelTileProps {
  label: string;
  points: TrendPoint[];
  seriesKey: LevelKey;
  stroke: string;
  fill: string;
  deltaLabel: (delta: number) => string;
  ariaLabel: string;
  testId: string;
  /** BCP-47 tag for the grouping separator; the app's language, not the browser's. */
  locale?: string;
}

function LevelTile({
  label,
  points,
  seriesKey,
  stroke,
  fill,
  deltaLabel,
  ariaLabel,
  testId,
  locale,
}: LevelTileProps) {
  const current = points.length > 0 ? points[points.length - 1][seriesKey] : 0;
  const first = points.length > 0 ? points[0][seriesKey] : 0;
  const delta = current - first;

  return (
    <div
      className="flex flex-col gap-2 rounded-md border p-4"
      data-testid={testId}
      data-current={current}
      data-delta={delta}
    >
      <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <span className="flex items-baseline gap-2">
        <span className="text-2xl font-semibold tabular-nums">
          {formatNumber(current, locale)}
        </span>
        {/* The arrow is the second signal: the sign is in the text too, so
         *  the direction survives without colour and without the glyph. */}
        <span
          className={cn(
            "text-xs font-medium tabular-nums",
            delta > 0 && "text-status-danger-foreground",
            delta < 0 && "text-status-success-foreground",
            delta === 0 && "text-muted-foreground",
          )}
        >
          {deltaLabel(delta)}
        </span>
      </span>
      <LevelSparkline
        points={points}
        seriesKey={seriesKey}
        stroke={stroke}
        fill={fill}
        label={ariaLabel}
      />
    </div>
  );
}

interface FlowTileProps {
  points: TrendPoint[];
  label: string;
  newLabel: string;
  resolvedLabel: string;
  totals: DashboardTrends["totals"];
  ariaLabel: string;
  dayLabel: (point: TrendPoint) => string;
  /** BCP-47 tag for the grouping separator; the app's language, not the browser's. */
  locale?: string;
}

/**
 * New against resolved, as diverging bars around a shared baseline.
 *
 * Direction carries the meaning before colour does — new grows upward,
 * resolved downward — so the pair reads under any colour vision, and the
 * legend names both. The two hues are a validated pair
 * (`--status-danger` / `--status-success`, ΔE 8.6 under deuteranopia, both
 * above the 3:1 non-text floor).
 */
function FlowTile({
  points,
  label,
  newLabel,
  resolvedLabel,
  totals,
  ariaLabel,
  dayLabel,
  locale,
}: FlowTileProps) {
  const max = Math.max(
    1,
    ...points.map((point) => Math.max(point.new_findings, point.resolved_findings)),
  );
  const mid = SPARK_HEIGHT / 2;
  const usable = mid - SPARK_PAD;
  const slot = points.length > 0 ? SPARK_WIDTH / points.length : SPARK_WIDTH;
  // A 2px surface gap between neighbouring bars, but never so wide that a
  // 90-day window leaves nothing to see.
  const barWidth = Math.max(slot * 0.5, Math.min(slot - 0.6, slot));

  return (
    <div
      className="flex flex-col gap-2 rounded-md border p-4"
      data-testid="trend-flow-tile"
      data-new={totals.new_findings}
      data-resolved={totals.resolved_findings}
    >
      <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      {/* `flex-wrap` because the row now carries two words as well as two
          numbers, and the three-column layout is narrowest just above the
          `sm` breakpoint, which neither the 390px narrow gate nor the
          desktop visual gate looks at. Without it the break happens inside
          a child instead of between them, splitting a label from the number
          it labels, which is the thing the labels were added to prevent. A
          line break between the pairs is the better of the two. */}
      <span className="flex flex-wrap items-baseline gap-3 text-sm font-medium tabular-nums">
        {/* B4: the signs used to be static text nodes, so a window where
            nothing was found and nothing was resolved read "+0 −0", which
            says two movements cancelled out rather than that nothing
            happened. `signed` already knew this; this tile did not use it.

            The words carry what the signs used to. Without them a quiet
            window reads "0 0" and only the colour says which is which,
            which is the failure this tile's own legend was written to
            avoid. */}
        <span className="text-status-danger-foreground">
          <span className="mr-1 font-normal text-muted-foreground">
            {newLabel}
          </span>
          {formatSignedDelta(totals.new_findings, locale)}
        </span>
        <span className="text-status-success-foreground">
          <span className="mr-1 font-normal text-muted-foreground">
            {resolvedLabel}
          </span>
          {formatSignedDelta(-totals.resolved_findings, locale)}
        </span>
      </span>
      <svg
        viewBox={`0 0 ${SPARK_WIDTH} ${SPARK_HEIGHT}`}
        preserveAspectRatio="none"
        className="h-9 w-full"
        role="img"
        aria-label={ariaLabel}
        data-testid="trend-flow-bars"
      >
        {points.map((point, index) => {
          const x = index * slot + (slot - barWidth) / 2;
          const up = (point.new_findings / max) * usable;
          const down = (point.resolved_findings / max) * usable;
          return (
            <g key={point.date}>
              {/* Native SVG hover text: every day is readable on the chart
               *  without a tooltip engine, and the same numbers are in the
               *  table below for anyone who cannot hover. */}
              <title>
                {dayLabel(point)}
              </title>
              {point.new_findings > 0 ? (
                <rect
                  x={x}
                  y={mid - up}
                  width={barWidth}
                  height={up}
                  fill="var(--status-danger)"
                  rx={0.5}
                />
              ) : null}
              {point.resolved_findings > 0 ? (
                <rect
                  x={x}
                  y={mid}
                  width={barWidth}
                  height={down}
                  fill="var(--status-success)"
                  rx={0.5}
                />
              ) : null}
            </g>
          );
        })}
        <line
          x1={0}
          y1={mid}
          x2={SPARK_WIDTH}
          y2={mid}
          stroke="hsl(var(--border))"
          strokeWidth={1}
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      <span className="flex flex-wrap gap-3 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden
            className="h-2 w-2 rounded-sm"
            style={{ background: "var(--status-danger)" }}
          />
          {newLabel}
        </span>
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden
            className="h-2 w-2 rounded-sm"
            style={{ background: "var(--status-success)" }}
          />
          {resolvedLabel}
        </span>
      </span>
    </div>
  );
}

/**
 * The same numbers as a table, for screen readers and for anyone the colour
 * fails. A chart without one is a claim only sighted users can check.
 */
function TrendTable({
  trends,
  caption,
  columns,
}: {
  trends: DashboardTrends;
  caption: string;
  columns: { date: string; newFindings: string; resolved: string; critical: string; kev: string };
}) {
  // The `sr-only` class belongs on a wrapper, not on the table. `sr-only`
  // works by shrinking an element to 1 px and clipping it, and a table
  // refuses to shrink below its min-content width — so the table kept its
  // natural size and the page gained a horizontal scrollbar on a phone for
  // something nobody can see. The narrow-viewport gate caught it; the a11y
  // and visual gates could not, because it is invisible and only affects
  // layout.
  return (
    <div className="sr-only">
      <table data-testid="trend-table">
        <caption>{caption}</caption>
        <thead>
          <tr>
            <th scope="col">{columns.date}</th>
            <th scope="col">{columns.newFindings}</th>
            <th scope="col">{columns.resolved}</th>
            <th scope="col">{columns.critical}</th>
            <th scope="col">{columns.kev}</th>
          </tr>
        </thead>
        <tbody>
          {trends.points.map((point) => (
            <tr key={point.date}>
              <th scope="row">{point.date}</th>
              <td>{point.new_findings}</td>
              <td>{point.resolved_findings}</td>
              <td>{point.critical_open}</td>
              <td>{point.kev_open}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function WindowPicker({
  value,
  onChange,
  label,
  optionLabel,
}: {
  value: TrendWindow;
  onChange: (next: TrendWindow) => void;
  label: string;
  optionLabel: (days: TrendWindow) => string;
}) {
  return (
    <div
      className="flex items-center gap-1 rounded-md border p-0.5"
      role="group"
      aria-label={label}
      data-testid="trend-window-picker"
    >
      {TREND_WINDOWS.map((days) => (
        <button
          key={days}
          type="button"
          onClick={() => onChange(days)}
          aria-pressed={days === value}
          data-testid={`trend-window-${days}`}
          className={cn(
            "rounded px-2 py-1 text-xs font-medium transition-colors duration-fast ease-out-soft",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
            // Neutral, not the brand accent: the accent whitelist covers the
            // sidebar's active item, tab indicators, progress and the login
            // panel, and a segmented control is none of those. Widening it
            // here is how a whitelist stops meaning anything.
            days === value
              ? "bg-accent text-foreground shadow-sm"
              : "text-muted-foreground hover:bg-accent/50",
          )}
        >
          {optionLabel(days)}
        </button>
      ))}
    </div>
  );
}

export function TrendsPanel() {
  const { t, i18n } = useTranslation("dashboard");
  // B4: these tiles used a bare toLocaleString, which follows the browser
  // rather than the language the app is running in.
  const locale = resolveLocale(i18n);
  const [days, setDays] = useState<TrendWindow>(30);
  const query = useDashboardTrends(days);

  const picker = (
    <WindowPicker
      value={days}
      onChange={setDays}
      label={t("trends.window_label")}
      optionLabel={(value) => t("trends.window_option", { days: value })}
    />
  );

  if (query.isPending) {
    return (
      <Card data-testid="trends-loading">
        <CardHeader className="flex flex-row items-center justify-between gap-4 pb-3">
          <CardTitle className="text-base">{t("trends.heading")}</CardTitle>
          {picker}
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-3">
          {[0, 1, 2].map((index) => (
            <Skeleton key={index} className="h-32" />
          ))}
        </CardContent>
      </Card>
    );
  }

  // A series that failed to load is not a flat series. Drawing zeros would
  // say the portfolio is clean, which is the one wrong answer this panel
  // must never give.
  if (query.isError) {
    return (
      <Card data-testid="trends-error">
        <CardHeader className="flex flex-row items-center justify-between gap-4 pb-3">
          <CardTitle className="text-base">{t("trends.heading")}</CardTitle>
          {picker}
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          {t("trends.error")}
        </CardContent>
      </Card>
    );
  }

  const trends = query.data;
  const nothingMeasured =
    trends.project_count === 0 ||
    trends.points.every((point) => point.scan_count === 0);
  // While a new window loads, the previous series stays on screen and dims.
  // Dropping back to skeletons would make the page below jump every time
  // someone compared 7 days against 30.
  const settling = query.isPlaceholderData && query.isFetching;

  return (
    <Card data-testid="trends-panel" data-settling={settling ? "true" : undefined}>
      <CardHeader className="flex flex-row items-center justify-between gap-4 pb-3">
        <CardTitle className="text-base">{t("trends.heading")}</CardTitle>
        {picker}
      </CardHeader>
      <CardContent
        className={cn(
          "space-y-3 transition-opacity duration-base ease-out-soft",
          settling && "opacity-60",
        )}
      >
        <div className="grid gap-3 sm:grid-cols-3">
          <LevelTile
            label={t("trends.critical_open")}
            points={trends.points}
            seriesKey="critical_open"
            stroke="var(--risk-critical)"
            fill="var(--risk-critical)"
            deltaLabel={(delta) =>
              t("trends.delta", { delta: formatSignedDelta(delta, locale) })
            }
            locale={locale}
            ariaLabel={t("trends.critical_aria", { days: trends.period_days })}
            testId="trend-critical-tile"
          />
          <LevelTile
            label={t("trends.kev_open")}
            points={trends.points}
            seriesKey="kev_open"
            stroke="var(--risk-medium-foreground)"
            fill="var(--risk-medium)"
            deltaLabel={(delta) =>
              t("trends.delta", { delta: formatSignedDelta(delta, locale) })
            }
            locale={locale}
            ariaLabel={t("trends.kev_aria", { days: trends.period_days })}
            testId="trend-kev-tile"
          />
          <FlowTile
            points={trends.points}
            label={t("trends.flow")}
            newLabel={t("trends.flow_new")}
            resolvedLabel={t("trends.flow_resolved")}
            totals={trends.totals}
            locale={locale}
            ariaLabel={t("trends.flow_aria", {
              days: trends.period_days,
              added: trends.totals.new_findings,
              resolved: trends.totals.resolved_findings,
            })}
            dayLabel={(point) =>
              t("trends.day_summary", {
                date: point.date,
                added: point.new_findings,
                resolved: point.resolved_findings,
              })
            }
          />
        </div>

        {/* The x axis in words. Three sparklines share one time span and none
         *  of them can carry tick labels at this size, so the span is stated
         *  once — otherwise the horizontal direction means nothing. */}
        <p className="flex flex-wrap justify-between gap-2 text-xs text-muted-foreground">
          <span data-testid="trends-footnote">
            {nothingMeasured ? t("trends.no_scans") : t("trends.carry_forward")}
          </span>
          <span className="tabular-nums" data-testid="trends-range">
            {trends.start_date} → {trends.end_date}
          </span>
        </p>

        <TrendTable
          trends={trends}
          caption={t("trends.table_caption", { days: trends.period_days })}
          columns={{
            date: t("trends.column.date"),
            newFindings: t("trends.column.new"),
            resolved: t("trends.column.resolved"),
            critical: t("trends.column.critical"),
            kev: t("trends.column.kev"),
          }}
        />
      </CardContent>
    </Card>
  );
}

