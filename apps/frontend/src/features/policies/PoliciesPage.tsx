/**
 * PoliciesPage — license policy editor (v2.2 c3, UI for the c1 API).
 *
 * The `/policies` screen lets a team_admin edit their team's license policy and
 * a super_admin edit any team policy or the org default. It follows the compact
 * enterprise density (40px rows, inline filters, skeleton loading) and opens the
 * editor in a right-side drawer whose state is URL-encoded
 * (`?policy=team:<id>` / `?policy=org:<id>`) so a hard reload reopens it.
 *
 * Team discovery, by role:
 *   - super_admin: the admin teams list (rich names) drives a team picker, plus
 *     the org-default entry. Every scope is editable.
 *   - everyone else: the membership-filtered policy list + the user's projects
 *     provide the team ids the caller can reach. The editor read returns 403 →
 *     read-only when the caller is a member but not a team_admin.
 *
 * No hardcoded English strings (every string via `t()`) and no hex literals
 * (Tailwind tokens / CSS vars only) — CLAUDE.md design system + i18n rules.
 */
import { useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FileText } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";

import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { AdminToast, type AdminToastMessage } from "@/features/admin/components/AdminToast";
import {
  PolicyEditorPanel,
  type PolicyScope,
} from "@/features/policies/PolicyEditorPanel";
import { useLicensePolicies } from "@/features/policies/useLicensePolicies";
import { listAdminTeams } from "@/features/admin/api/adminTeamsApi";
import { listProjects } from "@/lib/projectsApi";
import { cn } from "@/lib/utils";
import { usePermissions } from "@/hooks/usePermissions";

const DRAWER_PARAM = "policy";

interface DrawerTarget {
  scope: PolicyScope;
  id: string;
}

/** Parse `team:<id>` / `org:<id>` from the URL into a {@link DrawerTarget}. */
function parseDrawerParam(raw: string | null): DrawerTarget | null {
  if (!raw) return null;
  const sep = raw.indexOf(":");
  if (sep < 0) return null;
  const scope = raw.slice(0, sep);
  const id = raw.slice(sep + 1);
  if ((scope === "team" || scope === "org") && id.length > 0) {
    return { scope, id };
  }
  return null;
}

function encodeDrawerParam(target: DrawerTarget): string {
  return `${target.scope}:${target.id}`;
}

/** A selectable team option in the picker. */
interface TeamOption {
  id: string;
  /** Display label — a team name when known, else the truncated id. */
  label: string;
}

export function PoliciesPage() {
  const { t } = useTranslation("policies");
  const { isSuperAdmin } = usePermissions();

  const [searchParams, setSearchParams] = useSearchParams();
  const drawerTarget = parseDrawerParam(searchParams.get(DRAWER_PARAM));

  const [toast, setToast] = useState<AdminToastMessage | null>(null);
  const toastSeq = useRef(0);
  function notify(text: string, tone: "success" | "error", key?: string) {
    toastSeq.current += 1;
    setToast({ id: toastSeq.current, text, tone, key });
  }

  // --- visible existing policies (membership-filtered server-side) ---
  const policiesQuery = useLicensePolicies({ page: 1, page_size: 50 });
  const policies = useMemo(
    () => policiesQuery.data?.items ?? [],
    [policiesQuery.data],
  );

  // --- super_admin: admin teams for the picker (rich names) ---
  const adminTeamsQuery = useQuery({
    queryKey: ["admin", "teams", { page: 1, page_size: 200, scope: "policies" }],
    queryFn: () => listAdminTeams({ page: 1, page_size: 200 }),
    enabled: isSuperAdmin,
    staleTime: 30_000,
  });

  // --- non-admin: derive reachable team ids from their projects ---
  const myProjectsQuery = useQuery({
    // The backend caps GET /v1/projects at size=100 (le=100). Requesting 200
    // returned a 422 and broke the team picker for non-admins.
    queryKey: ["projects", { page: 1, size: 100, scope: "policies" }],
    queryFn: () => listProjects({ page: 1, size: 100 }),
    enabled: !isSuperAdmin,
    staleTime: 30_000,
  });

  // Build the team picker options + a team-name lookup.
  const { teamOptions, teamNameById, orgIds } = useMemo(() => {
    const nameById = new Map<string, string>();
    const orgSet = new Set<string>();
    const ids = new Set<string>();

    if (isSuperAdmin) {
      for (const team of adminTeamsQuery.data?.items ?? []) {
        ids.add(team.id);
        nameById.set(team.id, team.name);
      }
    } else {
      for (const project of myProjectsQuery.data?.items ?? []) {
        ids.add(project.team_id);
      }
    }
    // Always include teams that already have a policy.
    for (const p of policies) {
      if (p.team_id) ids.add(p.team_id);
      orgSet.add(p.organization_id);
    }

    const options: TeamOption[] = [...ids].sort().map((id) => ({
      id,
      label: nameById.get(id) ?? id.slice(0, 8),
    }));
    return {
      teamOptions: options,
      teamNameById: nameById,
      orgIds: [...orgSet].sort(),
    };
  }, [isSuperAdmin, adminTeamsQuery.data, myProjectsQuery.data, policies]);

  function openDrawer(target: DrawerTarget) {
    const next = new URLSearchParams(searchParams);
    next.set(DRAWER_PARAM, encodeDrawerParam(target));
    setSearchParams(next, { replace: false });
  }
  function closeDrawer() {
    const next = new URLSearchParams(searchParams);
    next.delete(DRAWER_PARAM);
    setSearchParams(next, { replace: false });
  }

  const [pickerTeamId, setPickerTeamId] = useState("");

  const drawerTitle =
    drawerTarget?.scope === "org"
      ? t("policies.editor.org_title")
      : t("policies.editor.team_title", {
          team:
            (drawerTarget && teamNameById.get(drawerTarget.id)) ??
            drawerTarget?.id.slice(0, 8) ??
            "",
        });

  return (
    <div className="flex h-full flex-col" data-testid="policies-page">
      <PageHeader
        title={t("policies.title")}
        description={t("policies.subtitle")}
      />

      {/* Inline toolbar: team picker (+ org default for super_admin) */}
      <div className="flex flex-wrap items-end gap-3 border-b bg-card px-6 py-3">
        <div className="flex flex-col gap-1">
          <Label
            htmlFor="policy-team-picker"
            className="text-xs text-muted-foreground"
          >
            {t("policies.toolbar.team_label")}
          </Label>
          <select
            id="policy-team-picker"
            data-testid="policy-team-picker"
            className="h-8 min-w-[14rem] rounded-md border border-input bg-background px-2 text-sm"
            value={pickerTeamId}
            onChange={(e) => setPickerTeamId(e.target.value)}
          >
            <option value="">{t("policies.toolbar.team_placeholder")}</option>
            {teamOptions.map((opt) => (
              <option key={opt.id} value={opt.id}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
        <Button
          size="sm"
          disabled={!pickerTeamId}
          onClick={() => openDrawer({ scope: "team", id: pickerTeamId })}
          data-testid="policy-edit-team"
        >
          {t("policies.toolbar.edit_team")}
        </Button>

        {isSuperAdmin && orgIds.length > 0 ? (
          <Button
            size="sm"
            variant="outline"
            onClick={() => openDrawer({ scope: "org", id: orgIds[0] })}
            data-testid="policy-edit-org"
          >
            {t("policies.toolbar.edit_org")}
          </Button>
        ) : null}
      </div>

      {/* Existing policies table */}
      <div className="flex-1 overflow-y-auto">
        {policiesQuery.isError ? (
          <div className="px-6 py-4">
            <Alert variant="destructive" data-testid="policies-error">
              <AlertDescription>{t("policies.errors.unknown")}</AlertDescription>
            </Alert>
          </div>
        ) : null}

        <table
          className="w-full text-sm"
          data-testid="policies-table"
          aria-busy={policiesQuery.isLoading}
        >
          <thead className="sticky top-0 bg-card">
            <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
              <th className="px-6 py-2">{t("policies.column.scope")}</th>
              <th className="px-3 py-2">{t("policies.column.name")}</th>
              <th className="px-3 py-2">{t("policies.column.status")}</th>
              <th className="px-3 py-2">{t("policies.column.overrides")}</th>
              <th className="px-3 py-2">{t("policies.column.exceptions")}</th>
              <th className="px-3 py-2 text-right">
                {t("policies.column.actions")}
              </th>
            </tr>
          </thead>
          <tbody data-testid="policies-tbody">
            {policiesQuery.isLoading
              ? Array.from({ length: 4 }).map((_, i) => (
                  <tr key={`skeleton-${i}`} className="border-b">
                    <td className="px-6 py-2" colSpan={6}>
                      <Skeleton className="h-5 w-full" />
                    </td>
                  </tr>
                ))
              : policies.map((policy) => {
                  const scope: PolicyScope = policy.team_id ? "team" : "org";
                  const target: DrawerTarget = {
                    scope,
                    id: policy.team_id ?? policy.organization_id,
                  };
                  return (
                    <tr
                      key={policy.id}
                      data-testid="policies-row"
                      data-policy-id={policy.id}
                      data-scope={scope}
                      className={cn(
                        "cursor-pointer border-b transition-colors duration-fast ease-out-soft hover:bg-accent/40 focus-within:bg-accent/40",
                      )}
                      style={{ height: "var(--table-row)" }}
                      tabIndex={0}
                      onClick={() => openDrawer(target)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          openDrawer(target);
                        }
                      }}
                    >
                      <td className="px-6">
                        <span className="font-medium">
                          {scope === "org"
                            ? t("policies.scope.org")
                            : teamNameById.get(policy.team_id ?? "") ??
                              (policy.team_id ?? "").slice(0, 8)}
                        </span>
                      </td>
                      <td className="px-3 text-muted-foreground">
                        {policy.name ?? t("policies.no_name")}
                      </td>
                      <td className="px-3">
                        <Badge
                          variant="outline"
                          className={cn(
                            policy.enabled
                              ? "border-emerald-300 bg-emerald-50 text-emerald-700"
                              : "border-muted bg-muted text-muted-foreground",
                          )}
                          data-enabled={policy.enabled}
                        >
                          {policy.enabled
                            ? t("policies.status.enabled")
                            : t("policies.status.disabled")}
                        </Badge>
                      </td>
                      <td className="px-3 font-mono text-xs text-muted-foreground">
                        {Object.keys(policy.category_overrides).length}
                      </td>
                      <td className="px-3 font-mono text-xs text-muted-foreground">
                        {policy.license_exceptions.length}
                      </td>
                      <td className="px-3 text-right">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={(e) => {
                            e.stopPropagation();
                            openDrawer(target);
                          }}
                          data-testid="policies-row-action"
                          aria-label={t("policies.column.actions")}
                        >
                          {t("policies.action.open")}
                        </Button>
                      </td>
                    </tr>
                  );
                })}

            {!policiesQuery.isLoading && policies.length === 0 ? (
              <tr>
                <td colSpan={6} className="p-0">
                  <EmptyState
                    data-testid="policies-empty"
                    icon={<FileText />}
                    title={t("policies.empty")}
                  />
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      <Sheet
        open={drawerTarget !== null}
        onOpenChange={(open) => {
          if (!open) closeDrawer();
        }}
      >
        <SheetContent
          side="right"
          className="flex w-full flex-col sm:max-w-lg"
          data-testid="policy-drawer"
        >
          <SheetHeader>
            <SheetTitle>{drawerTitle}</SheetTitle>
            <SheetDescription>
              {t("policies.editor.description")}
            </SheetDescription>
          </SheetHeader>
          <div className="mt-4 flex-1 overflow-hidden">
            {drawerTarget ? (
              <PolicyEditorPanel
                key={`${drawerTarget.scope}:${drawerTarget.id}`}
                scope={drawerTarget.scope}
                targetId={drawerTarget.id}
                canManage={isSuperAdmin || drawerTarget.scope === "team"}
                notify={notify}
              />
            ) : null}
          </div>
        </SheetContent>
      </Sheet>

      <AdminToast message={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}
