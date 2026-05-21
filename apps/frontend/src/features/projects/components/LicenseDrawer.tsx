import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  AffectedComponentByLicense,
  LicenseDetailResponse,
} from "@/features/projects/api/licensesApi";
import { useLicenseFinding } from "@/features/projects/api/useLicenseFinding";
import { LicenseCategoryBadge } from "@/features/projects/components/LicenseCategoryBadge";
import { LicenseKindBadge } from "@/features/projects/components/LicenseKindBadge";
import { ProblemError } from "@/lib/problem";
import { cn } from "@/lib/utils";

/**
 * LicenseDrawer — Phase 3 PR #12.
 *
 * Right-side Sheet drawer for the currently-selected license finding. Lazy
 * fetches `GET /v1/license_findings/{id}` and shows:
 *
 *   - Header: SPDX id (mono), name, category badge, finding kind, flag
 *     badges (OSI approved, FSF libre, deprecated SPDX id), reference URL.
 *   - ORT match: collapsible best-effort raw_data block. Render is text-only
 *     — `dangerouslySetInnerHTML` is forbidden because the ORT pipeline
 *     produces this payload from external license-detector output and the
 *     shape isn't contractual.
 *   - Affected components: list of component_versions carrying this license,
 *     with cross-link into the Components tab drawer (`?tab=components&drawer=<id>`)
 *     so the user can pivot from "what licenses do I ship?" to "what's in
 *     this exact component?" without navigating away from the project.
 *
 * Read-only — there are no actions, no transitions, no audit trail. The
 * drawer is a viewer.
 *
 * Accessibility: ESC closes (Radix), focus trap inside, "color is not the
 * only signal" — every category carries a label via LicenseCategoryBadge.
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

export interface LicenseDrawerProps {
  open: boolean;
  findingId: string | null;
  onOpenChange: (open: boolean) => void;
}

export function LicenseDrawer({
  open,
  findingId,
  onOpenChange,
}: LicenseDrawerProps) {
  const { t } = useTranslation("project_detail");
  const detail = useLicenseFinding(open ? findingId : null);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="flex w-full max-w-2xl flex-col gap-4 overflow-y-auto sm:max-w-2xl"
        data-testid="license-drawer"
      >
        <SheetHeader>
          <SheetTitle data-testid="license-drawer-title">
            {detail.data?.name ?? t("licenses.drawer.loading_title")}
          </SheetTitle>
          <SheetDescription>
            {detail.data
              ? t("licenses.drawer.subtitle", {
                  kind: t(`licenses.kind.${detail.data.finding_kind}`),
                })
              : t("licenses.drawer.loading_subtitle")}
          </SheetDescription>
        </SheetHeader>

        {detail.isLoading ? (
          <div
            className="flex flex-col gap-3"
            data-testid="license-drawer-loading"
          >
            <Skeleton className="h-6 w-1/3" />
            <Skeleton className="h-6 w-2/3" />
            <Skeleton className="h-32 w-full" />
          </div>
        ) : null}

        {detail.isError ? (
          <Alert variant="destructive" data-testid="license-drawer-error">
            <AlertDescription>
              {detail.error instanceof ProblemError
                ? detail.error.detail
                : t("licenses.errors.load_finding")}
            </AlertDescription>
          </Alert>
        ) : null}

        {detail.data ? (
          <div className="flex flex-col gap-5">
            <DrawerMetaSection detail={detail.data} />
            <DrawerOrtMatchSection ortMatch={detail.data.ort_match} />
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
// Subsections — broken out for readability + testability.
// ---------------------------------------------------------------------------

interface MetaProps {
  detail: LicenseDetailResponse;
}

function DrawerMetaSection({ detail }: MetaProps) {
  const { t } = useTranslation("project_detail");
  return (
    <section
      className="flex flex-col gap-2"
      data-testid="license-drawer-meta"
    >
      <div className="flex flex-wrap items-center gap-2">
        <LicenseCategoryBadge category={detail.category} />
        <span data-testid="license-drawer-kind">
          <LicenseKindBadge kind={detail.finding_kind} />
        </span>
        {detail.is_osi_approved ? (
          <Badge tone="info" data-testid="license-drawer-flag-osi">
            {t("licenses.drawer.flag.osi_approved")}
          </Badge>
        ) : null}
        {detail.is_fsf_libre ? (
          <Badge tone="info" data-testid="license-drawer-flag-fsf">
            {t("licenses.drawer.flag.fsf_libre")}
          </Badge>
        ) : null}
        {detail.is_deprecated_license_id ? (
          <Badge tone="medium" data-testid="license-drawer-flag-deprecated">
            {t("licenses.drawer.flag.deprecated")}
          </Badge>
        ) : null}
      </div>

      <div className="font-mono text-xs text-muted-foreground">
        <span className="mr-2 uppercase tracking-wide">
          {t("licenses.drawer.metadata.spdx_id_label")}
        </span>
        <span data-testid="license-drawer-spdx-id">
          {detail.spdx_id ?? t("licenses.row.no_spdx_id")}
        </span>
      </div>

      {isSafeUrl(detail.reference_url) ? (
        <div className="text-xs">
          <span className="mr-2 uppercase tracking-wide text-muted-foreground">
            {t("licenses.drawer.reference_url_label")}
          </span>
          <a
            href={detail.reference_url as string}
            target="_blank"
            rel="noopener noreferrer"
            className="font-mono text-primary underline-offset-4 hover:underline"
            data-testid="license-drawer-reference"
          >
            {detail.reference_url}
          </a>
        </div>
      ) : detail.reference_url ? (
        // URL was non-null but failed the http(s) scheme check — render as
        // plain text so the user still sees it without exposing a clickable
        // surface that could route to a non-web scheme.
        <div
          className="text-xs text-muted-foreground"
          data-testid="license-drawer-reference-unsafe"
        >
          <span className="mr-2 uppercase tracking-wide">
            {t("licenses.drawer.reference_url_label")}
          </span>
          <span className="font-mono">{detail.reference_url}</span>
        </div>
      ) : null}
    </section>
  );
}

interface OrtMatchProps {
  ortMatch: Record<string, unknown> | null;
}

/**
 * Render a defensively-stringified value. ORT raw_data may include nested
 * objects, arrays, numbers, booleans — we coerce to a one-line string with
 * `JSON.stringify` so the layout stays compact and no React-confusing
 * shape leaks through.
 *
 * Returns null for `undefined`/empty so the caller can skip the row.
 */
function stringifyOrtValue(value: unknown): string | null {
  if (value == null) return null;
  if (typeof value === "string") return value.length > 0 ? value : null;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return null;
  }
}

/**
 * Common ORT raw_data keys we know how to label. Anything outside this list
 * is rendered with its raw key so the user still sees it. We avoid blanket
 * `Object.entries` rendering for unknown keys to keep accidental leakage of
 * scan-internal structures (timing fields, opaque IDs) off the UI.
 */
const KNOWN_ORT_KEYS: ReadonlyArray<{ key: string; labelKey: string }> = [
  { key: "rule_name", labelKey: "licenses.drawer.ort_match.field.rule_name" },
  { key: "severity", labelKey: "licenses.drawer.ort_match.field.severity" },
  { key: "message", labelKey: "licenses.drawer.ort_match.field.message" },
  {
    key: "license_finding",
    labelKey: "licenses.drawer.ort_match.field.license_finding",
  },
  {
    key: "matched_text",
    labelKey: "licenses.drawer.ort_match.field.matched_text",
  },
  { key: "score", labelKey: "licenses.drawer.ort_match.field.score" },
  {
    key: "copyright",
    labelKey: "licenses.drawer.ort_match.field.copyright",
  },
];

function DrawerOrtMatchSection({ ortMatch }: OrtMatchProps) {
  const { t } = useTranslation("project_detail");
  const [expanded, setExpanded] = useState(false);

  if (ortMatch == null) {
    return (
      <section
        className="flex flex-col gap-2"
        data-testid="license-drawer-ort-match"
      >
        <h3 className="text-sm font-semibold">
          {t("licenses.drawer.section.ort_match")}
        </h3>
        <p className="text-sm text-muted-foreground">
          {t("licenses.drawer.ort_match.empty")}
        </p>
      </section>
    );
  }

  // Filter to known keys whose values can be rendered as React text.
  const rendered = KNOWN_ORT_KEYS.map(({ key, labelKey }) => ({
    key,
    labelKey,
    value: stringifyOrtValue(ortMatch[key]),
  })).filter((row) => row.value != null);

  if (rendered.length === 0) {
    // raw_data was non-null but didn't match any known shape — surface a
    // safe "we don't recognise this" message rather than dumping arbitrary
    // keys onto the UI.
    return (
      <section
        className="flex flex-col gap-2"
        data-testid="license-drawer-ort-match"
      >
        <h3 className="text-sm font-semibold">
          {t("licenses.drawer.section.ort_match")}
        </h3>
        <p
          className="text-sm text-muted-foreground"
          data-testid="license-drawer-ort-unrecognized"
        >
          {t("licenses.drawer.ort_match.unparseable")}
        </p>
      </section>
    );
  }

  return (
    <section
      className="flex flex-col gap-2"
      data-testid="license-drawer-ort-match"
    >
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">
          {t("licenses.drawer.section.ort_match")}
        </h3>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => setExpanded((v) => !v)}
          data-testid="license-drawer-ort-toggle"
        >
          {expanded
            ? t("licenses.drawer.ort_match.hide")
            : t("licenses.drawer.ort_match.show")}
        </Button>
      </div>
      {expanded ? (
        <dl
          className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 rounded-md border bg-muted/30 p-3 text-xs"
          data-testid="license-drawer-ort-fields"
        >
          {rendered.map(({ key, labelKey, value }) => (
            <div key={key} className="contents">
              <dt className="uppercase tracking-wide text-muted-foreground">
                {t(labelKey)}
              </dt>
              <dd
                className="whitespace-pre-wrap break-words font-mono"
                data-ort-key={key}
              >
                {value}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </section>
  );
}

interface AffectedSectionProps {
  components: AffectedComponentByLicense[];
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

  /**
   * Cross-link into the Components tab drawer. ComponentsTab (PR #10) reads
   * `?drawer=<componentVersionId>` and `?tab=components` from the same URL,
   * so we just rewrite the query string. The license drawer auto-closes
   * (its `?license=<id>` param is dropped) so we don't end up with two
   * drawers stacked.
   */
  function pivotToComponent(componentVersionId: string) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("tab", "components");
        next.set("drawer", componentVersionId);
        next.delete("license");
        return next;
      },
      { replace: false },
    );
    onClose();
  }

  const displayTotal = Math.max(total, components.length);

  return (
    <section
      className="flex flex-col gap-2"
      data-testid="license-drawer-affected"
    >
      <h3 className="text-sm font-semibold">
        {t("licenses.drawer.section.affected", { count: components.length })}
      </h3>
      {truncated ? (
        <p
          className="text-xs text-muted-foreground"
          data-testid="license-drawer-affected-truncated"
        >
          {t("licenses.drawer.affected_truncated", {
            shown: components.length,
            total: displayTotal,
          })}
        </p>
      ) : null}
      {components.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {t("licenses.drawer.affected.empty")}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {components.map((c) => (
            <li
              key={c.component_version_id}
              data-testid="license-drawer-affected-row"
              data-component-version-id={c.component_version_id}
              className="rounded-md border p-3"
            >
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <button
                  type="button"
                  onClick={() => pivotToComponent(c.component_version_id)}
                  data-testid="license-drawer-affected-link"
                  className={cn(
                    "font-medium text-primary underline-offset-4 hover:underline",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                  )}
                >
                  {c.component_name}
                </button>
                <span className="font-mono text-xs">{c.version}</span>
                <LicenseKindBadge kind={c.kind} />
              </div>
              {c.source_path ? (
                // First-party (scancode) evidence carries the exact file the
                // license was found in. Label it explicitly + render the path
                // in mono so the user can locate it in their tree.
                <div
                  className="mt-1 flex items-baseline gap-1.5 text-xs"
                  data-testid="license-drawer-affected-source-path"
                >
                  <span className="shrink-0 uppercase tracking-wide text-muted-foreground">
                    {t("licenses.drawer.affected.source_path_label")}
                  </span>
                  <span
                    className="truncate font-mono text-muted-foreground"
                    title={c.source_path}
                  >
                    {c.source_path}
                  </span>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
