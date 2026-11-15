// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { LinkExternalIcon } from "@primer/octicons-react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";

import { Alert, AlertDescription } from "@/components/primer/alert";
import { Button } from "@/components/primer/Button";
import { PrimerDialog } from "@/components/primer/PrimerDialog";
import { Skeleton } from "@/components/primer/Skeleton";
import { useComponent } from "@/features/projects/api/useComponent";
import { ComponentDetailBody } from "@/features/projects/components/ComponentDetailBody";
import { InheritedVerdictNote } from "@/features/projects/components/InheritedVerdictNote";
import { problemMessage } from "@/lib/problemMessage";

/**
 * ComponentDrawer — Phase 3 PR #10, refactored W10-E.
 *
 * Right-side Sheet drawer rendered for the currently-selected component.
 * Lazy-fetches `GET /v1/components/{id}` and shows:
 *
 *   - Header: name, version, purl, severity / license badges.
 *   - Vulnerabilities list: CVE id, severity, CVSS, title, description,
 *     fixed_version. CVE id rendered as plain text (no anchor) — links go
 *     out to NVD in a follow-up to keep XSS surface small.
 *   - raw_data accordion: collapsible JSON viewer (read-only `<pre>`,
 *     stringified through `JSON.stringify` so no HTML injection is possible).
 *
 * The detail body (meta panel, vulnerabilities list, raw_data accordion)
 * lives in {@link ComponentDetailBody} so the same data can also render on a
 * full-page route (W10-E `ComponentDetailPage`). This file owns only the
 * drawer-shell concerns:
 *
 *   - Sheet open/close + header (component name, version subtitle).
 *   - Loading skeleton + load-failed alert.
 *   - Detail-fetch query (`useComponent`).
 *   - "Open in full view" affordance (W10-E mirroring the vulnerabilities
 *     drawer pattern).
 *
 * Accessibility: ESC closes (Radix), focus trap inside, "color is not the
 * only signal" — every severity carries a label.
 */

export interface ComponentDrawerProps {
  open: boolean;
  componentId: string | null;
  onOpenChange: (open: boolean) => void;
  /**
   * Project id powering the "Open in full view" affordance (W10-E). When
   * supplied, the drawer header shows a button that closes the drawer and
   * navigates to the dedicated detail page
   * (`/projects/:projectId/components/:componentId`). The current drawer URL
   * is passed as `location.state.from` so the page's "Back to Components"
   * link returns the user to the exact list view (filters / pagination) they
   * came from. Omit to hide the affordance — historic call sites that don't
   * have a project context still work.
   */
  projectId?: string;
}

export function ComponentDrawer({
  open,
  componentId,
  onOpenChange,
  projectId,
}: ComponentDrawerProps) {
  const { t } = useTranslation("project_detail");
  const navigate = useNavigate();
  const location = useLocation();
  const detail = useComponent(open ? componentId : null);

  // W10-E: drawer → page affordance. Only enabled when the caller supplied a
  // `projectId` AND the component id is known — otherwise the destination URL
  // can't be built. We close the drawer first (`onOpenChange(false)`) so the
  // `?drawer=` query is dropped before the page mounts; then we navigate with
  // `state.from` set to the *current* URL so the page's "Back to Components"
  // link returns the user to the same filter/page they came from. We don't
  // try to round-trip the drawer back open from the page — that's a UX trap
  // (closing one surface only to re-open another while a third opens).
  const canOpenFullView =
    typeof projectId === "string" &&
    projectId.length > 0 &&
    typeof componentId === "string" &&
    componentId.length > 0;

  function handleOpenFullView() {
    if (!canOpenFullView) return;
    const from = `${location.pathname}${location.search}`;
    onOpenChange(false);
    navigate(`/projects/${projectId}/components/${componentId}`, {
      state: { from },
    });
  }

  return (
    <PrimerDialog
      open={open}
      onOpenChange={onOpenChange}
      title={detail.data?.name ?? t("drawer.loading_title")}
      titleTestId="component-drawer-title"
      description={
        detail.data
          ? t("drawer.subtitle", {
              version: detail.data.version,
            })
          : t("drawer.loading_subtitle")
      }
      headerExtra={
        canOpenFullView ? (
          <div className="pt-1">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={handleOpenFullView}
              aria-label={t("drawer.open_full_view")}
              data-testid="component-drawer-open-full"
              className="h-7 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground"
            >
              <LinkExternalIcon size={14} />
              {t("drawer.open_full_view")}
            </Button>
          </div>
        ) : null
      }
      position="right"
      width="xlarge"
      testId="component-drawer"
    >
      <div className="flex flex-col gap-6">
        {detail.isLoading ? (
          <div
            className="flex flex-col gap-3"
            data-testid="component-drawer-loading"
          >
            <Skeleton className="h-6 w-1/3" />
            <Skeleton className="h-6 w-2/3" />
            <Skeleton className="h-32 w-full" />
          </div>
        ) : null}

        {detail.isError ? (
          <Alert variant="destructive" data-testid="component-drawer-error">
            <AlertDescription>
              {problemMessage(detail.error, t, { action: "drawer.error" })}
            </AlertDescription>
          </Alert>
        ) : null}

        {/* Outside the body on purpose: the body renders without a query
            client, and this note asks the server what the project inherits. */}
        {detail.data ? (
          <InheritedVerdictNote
            projectId={detail.data.project_id}
            componentId={detail.data.component_id}
          />
        ) : null}
        {detail.data ? <ComponentDetailBody detail={detail.data} /> : null}
      </div>
    </PrimerDialog>
  );
}
