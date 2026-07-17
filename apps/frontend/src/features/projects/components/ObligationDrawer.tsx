import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  AffectedComponentByObligation,
  ObligationDetailResponse,
} from "@/features/projects/api/obligationsApi";
import { useObligation } from "@/features/projects/api/useObligation";
import { LicenseCategoryBadge } from "@/features/projects/components/LicenseCategoryBadge";
import { useAdvisoryTranslation } from "@/lib/advisoryTranslation";
import { cn } from "@/lib/utils";
import { ProblemError } from "@/lib/problem";

/**
 * ObligationDrawer — Phase 3 PR #13.
 *
 * Right-side Sheet drawer for the currently-selected obligation, scoped to
 * the project under inspection (URL: `?obligation=<id>`). Lazy-fetches
 * `GET /v1/projects/{project_id}/obligations/{id}` and renders:
 *
 *   - Header: parent license SPDX + name + category badge.
 *   - Obligation block: kind, full text, optional reference link
 *     (scheme-filtered to http(s)).
 *   - Affected components: component_versions in the latest scan that carry
 *     the parent license, with cross-link into the Components tab drawer
 *     (`?tab=components&drawer=<id>`).
 *
 * Read-only — the catalog is upstream-authoritative, so there are no
 * actions, transitions, or audit trail.
 */

const ALLOWED_LINK_SCHEMES = new Set(["http:", "https:"]);

function isSafeUrl(raw: unknown): raw is string {
  if (typeof raw !== "string" || raw.length === 0) return false;
  try {
    const parsed = new URL(raw);
    return ALLOWED_LINK_SCHEMES.has(parsed.protocol);
  } catch {
    return false;
  }
}

export interface ObligationDrawerProps {
  open: boolean;
  projectId: string | null;
  obligationId: string | null;
  onOpenChange: (open: boolean) => void;
}

export function ObligationDrawer({
  open,
  projectId,
  obligationId,
  onOpenChange,
}: ObligationDrawerProps) {
  const { t } = useTranslation("project_detail");
  const detail = useObligation(
    open ? projectId ?? undefined : undefined,
    open ? obligationId : null,
  );

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="flex w-full max-w-2xl flex-col gap-4 overflow-y-auto sm:max-w-2xl"
        data-testid="obligation-drawer"
      >
        <SheetHeader>
          <SheetTitle data-testid="obligation-drawer-title">
            {detail.data
              ? t("obligations.drawer.title", {
                  kind: t(`obligations.kind.${detail.data.kind}`, {
                    defaultValue: detail.data.kind,
                  }),
                })
              : t("obligations.drawer.loading_title")}
          </SheetTitle>
          <SheetDescription>
            {detail.data
              ? t("obligations.drawer.subtitle", {
                  license: detail.data.license_name,
                })
              : t("obligations.drawer.loading_subtitle")}
          </SheetDescription>
        </SheetHeader>

        {detail.isLoading ? (
          <div
            className="flex flex-col gap-3"
            data-testid="obligation-drawer-loading"
          >
            <Skeleton className="h-6 w-1/3" />
            <Skeleton className="h-6 w-2/3" />
            <Skeleton className="h-32 w-full" />
          </div>
        ) : null}

        {detail.isError ? (
          <Alert variant="destructive" data-testid="obligation-drawer-error">
            <AlertDescription>
              {detail.error instanceof ProblemError
                ? detail.error.detail
                : t("obligations.errors.load_detail")}
            </AlertDescription>
          </Alert>
        ) : null}

        {detail.data ? (
          <div className="flex flex-col gap-5">
            <DrawerMetaSection detail={detail.data} />
            <DrawerObligationBody detail={detail.data} />
            <DrawerAffectedSection
              components={detail.data.affected_components ?? []}
              total={detail.data.affected_components_total}
              truncated={detail.data.affected_components_truncated}
              onClose={() => onOpenChange(false)}
            />
          </div>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}

// ---------------------------------------------------------------------------
// Subsections
// ---------------------------------------------------------------------------

interface MetaProps {
  detail: ObligationDetailResponse;
}

function DrawerMetaSection({ detail }: MetaProps) {
  const { t } = useTranslation("project_detail");
  return (
    <section
      className="flex flex-col gap-2"
      data-testid="obligation-drawer-meta"
    >
      <div className="flex flex-wrap items-center gap-2">
        <LicenseCategoryBadge category={detail.license_category} />
        <Badge tone="info" data-testid="obligation-drawer-kind">
          {t(`obligations.kind.${detail.kind}`, { defaultValue: detail.kind })}
        </Badge>
      </div>

      <div className="font-mono text-xs text-muted-foreground">
        <span className="mr-2 uppercase tracking-wide">
          {t("obligations.drawer.metadata.license_label")}
        </span>
        <span data-testid="obligation-drawer-license-name">
          {detail.license_spdx_id ?? t("licenses.row.no_spdx_id")}
        </span>
        <span className="ml-2 text-muted-foreground">{detail.license_name}</span>
      </div>

      {isSafeUrl(detail.license_reference_url) ? (
        <div className="text-xs">
          <span className="mr-2 uppercase tracking-wide text-muted-foreground">
            {t("obligations.drawer.metadata.license_reference_label")}
          </span>
          <a
            href={detail.license_reference_url as string}
            target="_blank"
            rel="noopener noreferrer"
            className="font-mono text-primary underline-offset-4 hover:underline"
            data-testid="obligation-drawer-license-reference"
          >
            {detail.license_reference_url}
          </a>
        </div>
      ) : null}
    </section>
  );
}

function DrawerObligationBody({ detail }: MetaProps) {
  const { t } = useTranslation("project_detail");
  const { pick } = useAdvisoryTranslation();
  // C1a — Korean readers get the advisory rendering with the authoritative
  // English original one click away; everyone else sees English unchanged.
  const body = pick(detail.text, detail.text_ko);
  return (
    <section
      className="flex flex-col gap-2"
      data-testid="obligation-drawer-body"
    >
      <h3 className="text-sm font-semibold">
        {t("obligations.drawer.section.obligation")}
      </h3>
      <p
        className="whitespace-pre-wrap rounded-md border bg-muted/30 p-3 text-sm"
        data-testid="obligation-drawer-text"
      >
        {body.text}
      </p>
      {body.original !== null ? (
        <details
          className="rounded-md border bg-background px-3 py-2 text-xs"
          data-testid="obligation-drawer-original"
        >
          <summary className="cursor-pointer text-muted-foreground transition-colors duration-fast ease-out-soft hover:text-foreground">
            {t("obligations.drawer.translation_advisory")}
          </summary>
          <p
            className="mt-2 whitespace-pre-wrap text-sm"
            data-testid="obligation-drawer-original-text"
          >
            {body.original}
          </p>
        </details>
      ) : null}
      {detail.text_truncated ? (
        <p
          className="text-xs text-muted-foreground"
          data-testid="obligation-drawer-text-truncated"
        >
          {t("obligations.drawer.text_truncated")}
        </p>
      ) : null}
      {isSafeUrl(detail.link) ? (
        <div className="text-xs">
          <span className="mr-2 uppercase tracking-wide text-muted-foreground">
            {t("obligations.drawer.metadata.reference_label")}
          </span>
          <a
            href={detail.link as string}
            target="_blank"
            rel="noopener noreferrer"
            className="font-mono text-primary underline-offset-4 hover:underline"
            data-testid="obligation-drawer-reference"
          >
            {detail.link}
          </a>
        </div>
      ) : detail.link ? (
        <div
          className="text-xs text-muted-foreground"
          data-testid="obligation-drawer-reference-unsafe"
        >
          <span className="mr-2 uppercase tracking-wide">
            {t("obligations.drawer.metadata.reference_label")}
          </span>
          <span className="font-mono">{detail.link}</span>
        </div>
      ) : null}
    </section>
  );
}

interface AffectedSectionProps {
  components: AffectedComponentByObligation[];
  total: number;
  truncated: boolean;
  onClose: () => void;
}

function DrawerAffectedSection({
  components,
  total,
  truncated,
  onClose,
}: AffectedSectionProps) {
  const { t } = useTranslation("project_detail");
  const [, setSearchParams] = useSearchParams();

  function pivotToComponent(componentVersionId: string) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("tab", "components");
        next.set("drawer", componentVersionId);
        next.delete("obligation");
        return next;
      },
      { replace: false },
    );
    onClose();
  }

  // ``total`` is the un-capped row count; the rendered list is at most
  // ``components.length``. When the cap fired, surface the disclosure
  // message so the user can drop into the Components tab for the full set.
  const displayTotal = Math.max(total, components.length);

  return (
    <section
      className="flex flex-col gap-2"
      data-testid="obligation-drawer-affected"
    >
      <h3 className="text-sm font-semibold">
        {t("obligations.drawer.section.affected", { count: components.length })}
      </h3>
      {truncated ? (
        <p
          className="text-xs text-muted-foreground"
          data-testid="obligation-drawer-affected-truncated"
        >
          {t("obligations.drawer.affected_truncated", {
            shown: components.length,
            total: displayTotal,
          })}
        </p>
      ) : null}
      {components.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {t("obligations.drawer.affected.empty")}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {components.map((c) => (
            <li
              key={c.component_version_id}
              data-testid="obligation-drawer-affected-row"
              data-component-version-id={c.component_version_id}
              className="rounded-md border p-3"
            >
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <button
                  type="button"
                  onClick={() => pivotToComponent(c.component_version_id)}
                  data-testid="obligation-drawer-affected-link"
                  className={cn(
                    "font-medium text-primary underline-offset-4 hover:underline",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                  )}
                >
                  {c.component_name}
                </button>
                <span className="font-mono text-xs">{c.version}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
