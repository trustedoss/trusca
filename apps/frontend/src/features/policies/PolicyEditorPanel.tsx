/**
 * PolicyEditorPanel — fetch + save/reset wiring for one policy scope (c3).
 *
 * Renders inside the right-side drawer (Sheet) on the policies page. Given a
 * scope ("team" | "org") and the target id, it:
 *
 *   1. Reads the current effective policy (TanStack Query). A 404 means "no
 *      policy yet — start from a blank draft" (not an error). A 403 on the team
 *      read drops the panel into read-only mode (member, not team_admin).
 *   2. Seeds a local draft from the server policy (or a blank draft) and lets
 *      `PolicyEditorForm` mutate it.
 *   3. Saves via PUT (invalidate on success) and resets via DELETE (team only),
 *      surfacing success / 422 / 403 outcomes through the parent `notify`.
 *
 * Loading state = skeletons (CLAUDE.md). No hardcoded strings or hex literals.
 */
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { PolicyEditorForm } from "@/features/policies/PolicyEditorForm";
import {
  useOrgPolicy,
  useResetTeamPolicy,
  useSaveOrgPolicy,
  useSaveTeamPolicy,
  useTeamPolicy,
} from "@/features/policies/useLicensePolicies";
import {
  policyErrorMessageKey,
  policyErrorToken,
} from "@/features/policies/policyErrorMessage";
import {
  emptyPolicyDraft,
  type LicensePolicyOut,
  type LicensePolicyUpsertIn,
} from "@/lib/licensePoliciesApi";
import { ProblemError } from "@/lib/problem";

export type PolicyScope = "team" | "org";

interface NotifyFn {
  (text: string, tone: "success" | "error", key?: string): void;
}

interface Props {
  scope: PolicyScope;
  targetId: string;
  /** True when the caller is a super_admin (org scope always editable). */
  canManage: boolean;
  notify: NotifyFn;
}

/** Map a server policy row to an editable draft (drops read-only metadata). */
function toDraft(policy: LicensePolicyOut): LicensePolicyUpsertIn {
  return {
    name: policy.name,
    category_overrides: { ...policy.category_overrides },
    license_exceptions: policy.license_exceptions.map((ex) => ({ ...ex })),
    unknown_license_category: policy.unknown_license_category,
    compound_operator_strategy: { ...policy.compound_operator_strategy },
    enabled: policy.enabled,
  };
}

export function PolicyEditorPanel({ scope, targetId, canManage, notify }: Props) {
  const { t } = useTranslation("policies");

  const teamQuery = useTeamPolicy(scope === "team" ? targetId : null);
  const orgQuery = useOrgPolicy(scope === "org" ? targetId : null);
  const query = scope === "team" ? teamQuery : orgQuery;

  const saveTeam = useSaveTeamPolicy();
  const saveOrg = useSaveOrgPolicy();
  const resetTeam = useResetTeamPolicy();

  const [draft, setDraft] = useState<LicensePolicyUpsertIn>(emptyPolicyDraft);

  // A 404 = no policy yet (draft fresh). A 403 = read-only (member only).
  const status =
    query.error instanceof ProblemError ? query.error.status : undefined;
  const isNotFound = status === 404;
  const isForbidden = status === 403;
  const isLoadError =
    query.isError && !isNotFound && !isForbidden ? query.error : null;

  // Read-only when the caller cannot manage OR the read returned 403.
  const readOnly = !canManage || isForbidden;

  // Seed the draft once the query settles. We key on the data identity so a
  // re-fetch (after save) re-seeds, while local edits between fetches persist.
  const seed = useMemo<LicensePolicyUpsertIn>(() => {
    if (query.data) return toDraft(query.data);
    return emptyPolicyDraft();
  }, [query.data]);

  useEffect(() => {
    // Re-seed when the target scope changes or the server data arrives.
    setDraft(seed);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, targetId, query.data]);

  const isSaving = saveTeam.isPending || saveOrg.isPending;
  const isResetting = resetTeam.isPending;

  async function handleSave() {
    try {
      if (scope === "team") {
        await saveTeam.mutateAsync({ teamId: targetId, payload: draft });
      } else {
        await saveOrg.mutateAsync({
          organizationId: targetId,
          payload: draft,
        });
      }
      notify(t("policies.toast.saved"), "success", "saved");
    } catch (err) {
      notify(t(policyErrorMessageKey(err)), "error", policyErrorToken(err));
    }
  }

  async function handleReset() {
    try {
      await resetTeam.mutateAsync(targetId);
      setDraft(emptyPolicyDraft());
      notify(t("policies.toast.reset"), "success", "reset");
    } catch (err) {
      notify(t(policyErrorMessageKey(err)), "error", policyErrorToken(err));
    }
  }

  if (query.isLoading) {
    return (
      <div className="flex flex-col gap-3" data-testid="policy-editor-loading">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  if (isLoadError) {
    return (
      <Alert variant="destructive" data-testid="policy-editor-error">
        <AlertDescription>{t("policies.errors.unknown")}</AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="flex h-full flex-col" data-testid="policy-editor-panel">
      {isNotFound ? (
        <Alert className="mb-4" data-testid="policy-editor-no-policy">
          <AlertDescription>
            {t("policies.editor.no_policy_hint")}
          </AlertDescription>
        </Alert>
      ) : null}

      {readOnly ? (
        <Alert className="mb-4" data-testid="policy-editor-readonly">
          <AlertDescription>
            {isForbidden
              ? t("policies.editor.readonly_member")
              : t("policies.editor.readonly_role")}
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="flex-1 overflow-y-auto pr-1">
        <PolicyEditorForm
          draft={draft}
          onChange={setDraft}
          readOnly={readOnly}
        />
      </div>

      {!readOnly ? (
        <div className="mt-4 flex shrink-0 items-center justify-between border-t pt-4">
          {scope === "team" ? (
            <Button
              type="button"
              variant="outline"
              onClick={handleReset}
              disabled={isResetting || isSaving || isNotFound}
              data-testid="policy-reset"
            >
              {isResetting
                ? t("policies.editor.resetting")
                : t("policies.editor.reset")}
            </Button>
          ) : (
            <span />
          )}
          <Button
            type="button"
            onClick={handleSave}
            disabled={isSaving || isResetting}
            data-testid="policy-save"
          >
            {isSaving ? t("policies.editor.saving") : t("policies.editor.save")}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
