/**
 * RemediationTab — v2.2 Track B (b3 frontend): UI for the npm remediation
 * dry-run (b2) + opt-in automated PR (b3).
 *
 * Two stacked cards:
 *   1. Preview — runs the dry-run (POST, on-demand) and renders the proposed
 *      package bumps (current → recommended), the `manifest_source` indicator,
 *      the warnings (esp. lockfile-regeneration-required), and the no-change /
 *      no-manifest empty states.
 *   2. Create PR — team_admin-only. When the project is NOT opted in (409) the
 *      button is replaced with inline guidance rather than crashing. On success
 *      the created PR is shown with a SAFE external link (no
 *      dangerouslySetInnerHTML). Below it, the list of existing remediation PRs.
 *
 * Role gate: the *project-team-scoped* role (`current_user_role` on the
 * overview query) must be `team_admin` / `super_admin` to open a PR — mirrors
 * the VEX import / suppression gate. The backend re-enforces it (403), so this
 * is a UX affordance, not the security boundary.
 *
 * Security: every string the API echoes back (warning detail, package names,
 * `pr_url`) is rendered through React's default text escaping. `pr_url` is
 * rendered as an `<a href>` with `rel="noopener noreferrer"` — never via
 * `dangerouslySetInnerHTML`.
 *
 * Design tokens only (no hex literals); compact density; skeleton loading.
 */
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectOverview } from "@/features/projects/api/useProjectOverview";
import {
  useCreateNpmPullRequest,
  useNpmDryRun,
  useRemediationPullRequests,
} from "@/features/projects/api/useRemediation";
import { RemediationPrStatusBadge } from "@/features/projects/components/RemediationPrStatusBadge";
import { ProblemError } from "@/lib/problem";
import type {
  ManifestSource,
  NpmDryRunResponse,
  RemediationPullRequest,
} from "@/lib/remediationApi";

interface RemediationTabProps {
  projectId: string;
}

/** Pull an RFC 7807 detail (or generic message) off any thrown error. */
function errorDetail(err: unknown): string | null {
  if (!err) return null;
  if (err instanceof ProblemError) return err.detail;
  return err instanceof Error ? err.message : String(err);
}

export function RemediationTab({ projectId }: RemediationTabProps) {
  const { t } = useTranslation("remediation");

  // Shares the ["projects", projectId, "overview"] key with the page-level
  // fetch so TanStack Query dedupes it (no extra request). Default to the
  // least-privileged `developer` until it resolves.
  const overview = useProjectOverview(projectId);
  const projectRole = overview.data?.current_user_role ?? "developer";
  const canCreatePr =
    projectRole === "team_admin" || projectRole === "super_admin";

  const dryRun = useNpmDryRun(projectId);
  const createPr = useCreateNpmPullRequest(projectId);
  const prList = useRemediationPullRequests(projectId);

  const preview: NpmDryRunResponse | undefined = dryRun.data;

  // The create-PR path surfaces a 409 (not opted in) and a 403 (not a team
  // admin) as graceful inline guidance — never a crash.
  const createErr =
    createPr.error instanceof ProblemError ? createPr.error : null;
  const notOptedIn = createErr?.status === 409;
  const forbidden = createErr?.status === 403;
  const otherCreateError =
    createErr && !notOptedIn && !forbidden
      ? createErr.detail
      : !createErr && createPr.error
        ? errorDetail(createPr.error)
        : null;

  const createdPr: RemediationPullRequest | null | undefined = createPr.data;

  return (
    <div
      className="mx-auto max-w-4xl space-y-6 p-6"
      data-testid="remediation-tab"
    >
      {/* ---------------------------------------------------------------- */}
      {/* Card 1 — Preview (dry-run)                                       */}
      {/* ---------------------------------------------------------------- */}
      <Card data-testid="remediation-preview-card">
        <CardHeader>
          <CardTitle>{t("preview.title")}</CardTitle>
          <CardDescription>{t("preview.subtitle")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-3">
            <Button
              type="button"
              size="sm"
              onClick={() => dryRun.mutate()}
              disabled={dryRun.isPending}
              data-testid="remediation-preview-button"
            >
              {dryRun.isPending
                ? t("preview.action.running")
                : t("preview.action.run")}
            </Button>
            {preview ? (
              <ManifestSourceBadge source={preview.manifest_source} />
            ) : null}
          </div>

          {dryRun.isPending ? (
            <div className="space-y-2" data-testid="remediation-preview-loading">
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-9 w-2/3" />
            </div>
          ) : null}

          {dryRun.isError ? (
            <Alert variant="destructive" data-testid="remediation-preview-error">
              <AlertDescription>{errorDetail(dryRun.error)}</AlertDescription>
            </Alert>
          ) : null}

          {preview && !dryRun.isPending ? (
            <PreviewResult preview={preview} />
          ) : null}
        </CardContent>
      </Card>

      {/* ---------------------------------------------------------------- */}
      {/* Card 2 — Create PR + existing PR list                            */}
      {/* ---------------------------------------------------------------- */}
      <Card data-testid="remediation-pr-card">
        <CardHeader>
          <CardTitle>{t("pr.title")}</CardTitle>
          <CardDescription>{t("pr.subtitle")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {!canCreatePr ? (
            <Alert data-testid="remediation-pr-role-gated">
              <AlertDescription>{t("pr.role_gated")}</AlertDescription>
            </Alert>
          ) : notOptedIn ? (
            <Alert data-testid="remediation-pr-not-opted-in">
              <AlertDescription>{t("pr.not_opted_in")}</AlertDescription>
            </Alert>
          ) : forbidden ? (
            // Defensive: the UI gate said yes but the backend said no (e.g. the
            // role changed mid-session). Surface the guidance, not a crash.
            <Alert data-testid="remediation-pr-forbidden">
              <AlertDescription>{t("pr.forbidden")}</AlertDescription>
            </Alert>
          ) : (
            <Button
              type="button"
              size="sm"
              onClick={() => createPr.mutate()}
              disabled={createPr.isPending}
              data-testid="remediation-create-pr-button"
            >
              {createPr.isPending
                ? t("pr.action.creating")
                : t("pr.action.create")}
            </Button>
          )}

          {otherCreateError ? (
            <Alert variant="destructive" data-testid="remediation-create-error">
              <AlertDescription>{otherCreateError}</AlertDescription>
            </Alert>
          ) : null}

          {createPr.isSuccess && createdPr === null ? (
            <Alert data-testid="remediation-create-noop">
              <AlertDescription>{t("pr.noop")}</AlertDescription>
            </Alert>
          ) : null}

          {createdPr ? (
            <Alert data-testid="remediation-create-success">
              <AlertDescription>
                {t("pr.created")}{" "}
                <PrLink pr={createdPr} testId="remediation-created-pr-link" />
              </AlertDescription>
            </Alert>
          ) : null}

          <PullRequestList
            isLoading={prList.isLoading}
            isError={prList.isError}
            error={prList.error}
            items={prList.data?.items ?? []}
          />
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Preview result — bumps table + warnings + empty states.
// ---------------------------------------------------------------------------

function PreviewResult({ preview }: { preview: NpmDryRunResponse }) {
  const { t } = useTranslation("remediation");

  if (preview.manifest_source === "none" || !preview.manifest_found) {
    return (
      <Alert data-testid="remediation-no-manifest">
        <AlertDescription>{t("preview.no_manifest")}</AlertDescription>
      </Alert>
    );
  }

  if (!preview.changed || preview.recommendations.length === 0) {
    return (
      <div className="space-y-3">
        <Alert data-testid="remediation-no-changes">
          <AlertDescription>{t("preview.no_changes")}</AlertDescription>
        </Alert>
        <WarningList warnings={preview.warnings} />
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <table
        className="w-full border-collapse text-sm"
        data-testid="remediation-bumps-table"
      >
        <thead>
          <tr className="border-b text-left text-xs text-muted-foreground">
            <th className="py-2 pr-4 font-medium">
              {t("preview.column.package")}
            </th>
            <th className="py-2 pr-4 font-medium">
              {t("preview.column.current")}
            </th>
            <th className="py-2 font-medium">
              {t("preview.column.recommended")}
            </th>
          </tr>
        </thead>
        <tbody>
          {preview.recommendations.map((rec) => (
            <tr
              key={rec.package}
              className="border-b"
              style={{ height: "var(--table-row)" }}
              data-testid="remediation-bump-row"
              data-package={rec.package}
            >
              <td className="py-2 pr-4 font-mono text-xs">{rec.package}</td>
              <td className="py-2 pr-4 font-mono text-xs text-muted-foreground">
                {rec.current_version}
              </td>
              <td className="py-2 font-mono text-xs">
                <span className="text-risk-low">
                  {rec.recommended_version}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <WarningList warnings={preview.warnings} />
    </div>
  );
}

function WarningList({
  warnings,
}: {
  warnings: NpmDryRunResponse["warnings"];
}) {
  const { t } = useTranslation("remediation");
  if (warnings.length === 0) return null;
  return (
    <ul className="space-y-1.5" data-testid="remediation-warnings">
      {warnings.map((w, idx) => (
        <li
          key={`${w.code}-${w.package ?? "_"}-${idx}`}
          className="flex items-start gap-2 text-xs"
          data-testid="remediation-warning"
          data-code={w.code}
        >
          <Badge variant="outline" tone="medium" className="shrink-0">
            {t("preview.warning_label")}
          </Badge>
          <span className="text-muted-foreground">
            {w.package ? (
              <span className="font-mono text-foreground">{w.package}: </span>
            ) : null}
            {w.detail}
          </span>
        </li>
      ))}
    </ul>
  );
}

function ManifestSourceBadge({ source }: { source: ManifestSource }) {
  const { t } = useTranslation("remediation");
  return (
    <Badge
      variant="muted"
      data-testid="remediation-manifest-source"
      data-source={source}
    >
      {t("preview.manifest_source", {
        source: t(`preview.manifest_source_value.${source}`),
      })}
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// PR list.
// ---------------------------------------------------------------------------

function PullRequestList({
  isLoading,
  isError,
  error,
  items,
}: {
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  items: RemediationPullRequest[];
}) {
  const { t } = useTranslation("remediation");

  return (
    <section className="space-y-2" data-testid="remediation-pr-list">
      <h3 className="text-sm font-semibold">{t("pr.list.title")}</h3>

      {isLoading ? (
        <div className="space-y-2" data-testid="remediation-pr-list-loading">
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
        </div>
      ) : isError ? (
        <Alert variant="destructive" data-testid="remediation-pr-list-error">
          <AlertDescription>{errorDetail(error)}</AlertDescription>
        </Alert>
      ) : items.length === 0 ? (
        <p
          className="text-xs text-muted-foreground"
          data-testid="remediation-pr-list-empty"
        >
          {t("pr.list.empty")}
        </p>
      ) : (
        <ul className="divide-y rounded-md border">
          {items.map((pr) => (
            <li
              key={pr.id}
              className="flex items-center justify-between gap-3 px-3 py-2"
              data-testid="remediation-pr-row"
              data-pr-id={pr.id}
            >
              <div className="flex min-w-0 flex-col gap-0.5">
                <PrLink pr={pr} testId="remediation-pr-link" />
                <span className="font-mono text-[10px] text-muted-foreground">
                  {pr.repository_full_name} · {formatTime(pr.created_at)}
                </span>
              </div>
              <RemediationPrStatusBadge status={pr.status} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * Render the PR as a safe external link when a URL exists, else a plain label.
 * `target="_blank" rel="noopener noreferrer"` per the brief; never
 * `dangerouslySetInnerHTML`.
 */
function PrLink({
  pr,
  testId,
}: {
  pr: RemediationPullRequest;
  testId: string;
}) {
  const { t } = useTranslation("remediation");
  const label =
    pr.pr_number != null
      ? t("pr.link_label", { number: pr.pr_number })
      : t("pr.link_label_pending");

  if (!pr.pr_url) {
    return (
      <span className="text-sm" data-testid={`${testId}-pending`}>
        {label}
      </span>
    );
  }

  return (
    <a
      href={pr.pr_url}
      target="_blank"
      rel="noopener noreferrer"
      className="truncate text-sm font-medium text-risk-low hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      data-testid={testId}
    >
      {label}
    </a>
  );
}

function formatTime(iso: string): string {
  // Stable, locale-aware short form; falls back to the raw ISO if unparseable.
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}
