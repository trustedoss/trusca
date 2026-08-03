// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useDashboardPortfolio,
  type PortfolioProject,
} from "@/features/dashboard/api/portfolio";
import { cn } from "@/lib/utils";

/**
 * The portfolio by team — who is carrying the risk.
 *
 * A project list answers "which project is worst". This answers "which team",
 * which is a question that needs an organisation, teams, and projects that
 * outlive a single scan. A tool that runs once against a directory has no
 * teams to group by.
 *
 * Not a colour-scale heatmap. Severity is an ordinal domain scale with fixed
 * meanings, not a continuous magnitude, so a cell is tinted by its worst
 * bucket and prints that bucket's count as text. The tint is a second signal:
 * the count and the label carry the state on their own.
 *
 * The one distinction the design has to protect is that zero means two
 * different things. A project nobody has scanned and a project that came back
 * clean have identical numbers; only `scanned` separates them, and painting
 * them alike would tell the reader an unexamined project is fine.
 */

type Bucket = "critical" | "high" | "medium" | "low";

const BUCKETS: Bucket[] = ["critical", "high", "medium", "low"];

/** Tint + dot per bucket.
 *
 *  W16 wrote these as `risk-tint-*` classes hand-declared in `index.css`,
 *  because `bg-risk-critical/10` emitted no rule and painted nothing. G0-7
 *  fixed that in `tailwind.config.ts`, so the ordinary spelling is back and
 *  the bespoke classes are gone. */
const CELL_TONE: Record<Bucket | "clean" | "unscanned", string> = {
  critical: "border-risk-critical/40 bg-risk-critical/10",
  high: "border-risk-high/40 bg-risk-high/10",
  medium: "border-risk-medium/40 bg-risk-medium/10",
  low: "border-risk-low/40 bg-risk-low/10",
  clean: "border-border bg-card",
  // Deliberately not a risk tint and deliberately not the clean surface: an
  // unmeasured project is its own state, and dashed edges say "no data" in a
  // way a colour cannot.
  unscanned: "border-dashed border-border bg-muted/40",
};

const DOT_TONE: Record<Bucket, string> = {
  critical: "bg-risk-critical",
  high: "bg-risk-high",
  medium: "bg-risk-medium",
  low: "bg-risk-low",
};

function worstBucket(project: PortfolioProject): Bucket | null {
  return BUCKETS.find((bucket) => project[bucket] > 0) ?? null;
}

function ProjectCell({
  project,
  bucketLabel,
  unscannedLabel,
  cleanLabel,
  breakdown,
  unscannedTooltip,
}: {
  project: PortfolioProject;
  bucketLabel: (bucket: Bucket) => string;
  unscannedLabel: string;
  cleanLabel: string;
  breakdown: string;
  unscannedTooltip: string;
}) {
  const worst = worstBucket(project);
  const tone = !project.scanned ? "unscanned" : (worst ?? "clean");

  return (
    <Link
      to={`/projects/${project.project_id}`}
      data-testid={`portfolio-cell-${project.project_id}`}
      data-tone={tone}
      // The cell text and the tint both say "never scanned"; the tooltip has
      // to as well. "Critical 0 · High 0 · …" on an unmeasured project is a
      // positive claim that it was looked at and found empty — the exact
      // reading this component exists to prevent, surviving on the one
      // surface nobody thought to check.
      title={project.scanned ? breakdown : unscannedTooltip}
      className={cn(
        "flex min-w-0 items-center justify-between gap-2 rounded-md border px-3 py-2",
        "text-sm transition-colors duration-fast ease-out-soft hover:bg-accent",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        CELL_TONE[tone],
      )}
    >
      <span className="flex min-w-0 items-center gap-2">
        {worst ? (
          <span
            aria-hidden
            className={cn("h-2 w-2 shrink-0 rounded-full", DOT_TONE[worst])}
          />
        ) : null}
        <span className="truncate">{project.project_name}</span>
      </span>
      {/* Near-black on a tint, muted on the plain surfaces. `text-muted-
       *  foreground` everywhere was the obvious spelling and the axe gate
       *  rejected it: the muted grey clears AA on white and misses it on a
       *  10 % severity tint, which is precisely the case a machine catches
       *  and a reviewer does not. */}
      <span
        className={cn(
          "shrink-0 text-xs tabular-nums",
          tone === "critical" || tone === "high" || tone === "medium" || tone === "low"
            ? "text-foreground"
            : "text-muted-foreground",
        )}
      >
        {!project.scanned
          ? unscannedLabel
          : worst
            ? `${bucketLabel(worst)} ${project[worst]}`
            : cleanLabel}
      </span>
    </Link>
  );
}

export function PortfolioGrid() {
  const { t } = useTranslation("dashboard");
  const query = useDashboardPortfolio();

  if (query.isPending) {
    return (
      <Card data-testid="portfolio-loading">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">{t("portfolio.heading")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {[0, 1].map((index) => (
            <Skeleton key={index} className="h-16" />
          ))}
        </CardContent>
      </Card>
    );
  }

  // An empty grid and a failed one look identical once rendered, and only one
  // of them means "you have nothing to worry about".
  if (query.isError) {
    return (
      <Card data-testid="portfolio-error">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">{t("portfolio.heading")}</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          {t("portfolio.error")}
        </CardContent>
      </Card>
    );
  }

  const portfolio = query.data;

  return (
    <Card data-testid="portfolio-grid">
      <CardHeader className="pb-3">
        <CardTitle className="text-base">{t("portfolio.heading")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {portfolio.teams.length === 0 ? (
          <p className="text-sm text-muted-foreground" data-testid="portfolio-empty">
            {t("portfolio.empty")}
          </p>
        ) : null}

        {portfolio.teams.map((team) => (
          <div key={team.team_id} data-testid={`portfolio-team-${team.team_id}`}>
            {/* `truncate` needs `min-w-0` to bite on a flex child, and the
             *  count needs `shrink-0` so a long team name cannot squeeze it
             *  out of the row. Without both, a phone-width viewport gains a
             *  sideways scrollbar. */}
            <h3 className="mb-2 flex items-baseline gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <span className="min-w-0 truncate">{team.team_name}</span>
              <span className="shrink-0 tabular-nums font-normal">
                {t("portfolio.team_projects", { count: team.project_count })}
              </span>
            </h3>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {team.projects.map((project) => (
                <ProjectCell
                  key={project.project_id}
                  project={project}
                  bucketLabel={(bucket) => t(`portfolio.bucket.${bucket}`)}
                  unscannedLabel={t("portfolio.never_scanned")}
                  cleanLabel={t("portfolio.clean")}
                  breakdown={t("portfolio.breakdown", {
                    critical: project.critical,
                    high: project.high,
                    medium: project.medium,
                    low: project.low,
                    scanned: project.last_scan_at
                      ? new Date(project.last_scan_at).toLocaleDateString()
                      : "—",
                  })}
                  unscannedTooltip={t("portfolio.never_scanned_hint")}
                />
              ))}
            </div>
            {team.projects.length < team.project_count ? (
              <p
                className="mt-2 text-xs text-muted-foreground"
                data-testid={`portfolio-team-truncated-${team.team_id}`}
              >
                {t("portfolio.team_truncated", {
                  shown: team.projects.length,
                  total: team.project_count,
                })}
              </p>
            ) : null}
          </div>
        ))}

        {/* Saying what was left out is not a footnote here: a grid showing the
         *  worst twelve reads exactly like a grid showing everything, and the
         *  conclusion the reader draws about the rest is opposite in each case. */}
        {portfolio.truncated ? (
          <p className="text-xs text-muted-foreground" data-testid="portfolio-truncated">
            {t("portfolio.truncated", {
              shown: portfolio.shown_project_count,
              total: portfolio.project_count,
              shownTeams: portfolio.shown_team_count,
              teams: portfolio.team_count,
            })}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
