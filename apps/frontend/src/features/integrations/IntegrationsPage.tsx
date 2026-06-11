/**
 * IntegrationsPage — chore C.
 *
 * Renders the "Integrations" route. Two stacked sections:
 *   1. API keys — paginated table + create dialog + one-shot reveal dialog
 *      + revoke confirmation. Backed by /v1/api-keys.
 *   2. Webhook URLs — informational copy of the GitHub / GitLab receiver
 *      URLs so users know where to configure their repository hooks.
 *
 * Design: matches the compact 40 px row density used by ScansPage /
 * ApprovalsPage / AdminScansPage. No hardcoded color literals — every
 * tone comes from existing Tailwind tokens. All visible strings flow
 * through `t()` (CLAUDE.md i18n rule).
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { PageHeader } from "@/components/PageHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { AdminToast, type AdminToastMessage } from "@/features/admin/components/AdminToast";
import { CreateApiKeyDialog } from "@/features/integrations/CreateApiKeyDialog";
import { RevealApiKeyDialog } from "@/features/integrations/RevealApiKeyDialog";
import { RevokeApiKeyDialog } from "@/features/integrations/RevokeApiKeyDialog";
import { useApiKeys } from "@/features/integrations/useApiKeys";
import { usePermissions } from "@/hooks/usePermissions";
import { createApiKey, revokeApiKey } from "@/lib/apiKeysApi";
import { getApiBase } from "@/lib/apiBase";
import { ProblemError } from "@/lib/problem";
import RelativeTime from "@/components/RelativeTime";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/stores/authStore";
import type {
  APIKeyCreateOut,
  APIKeyCreatePayload,
  APIKeyListItem,
  APIKeyScope,
} from "@/types/apiKey";

const PAGE_SIZE = 20;

/** Total <td> count per row — keep skeleton / empty colSpan in sync. */
const COLUMN_COUNT = 9;

/**
 * Revocation status badge (L-17). Color is never the only signal — the
 * translated label rides along, matching the a11y rule for risk badges.
 */
function StatusBadge({ revoked }: { revoked: boolean }) {
  const { t } = useTranslation("integrations");
  return (
    <Badge
      variant="outline"
      className={cn(
        revoked
          ? "border-red-300 bg-red-50 text-red-700"
          : "border-emerald-300 bg-emerald-50 text-emerald-700",
      )}
      data-testid="integrations-key-status"
      data-status={revoked ? "revoked" : "active"}
    >
      {revoked ? t("api_keys.status.revoked") : t("api_keys.status.active")}
    </Badge>
  );
}

function ScopeBadge({ scope }: { scope: APIKeyScope }) {
  const { t } = useTranslation("integrations");
  const toneClass: Record<APIKeyScope, string> = {
    org: "border-purple-300 bg-purple-50 text-purple-700",
    team: "border-blue-300 bg-blue-50 text-blue-700",
    project: "border-emerald-300 bg-emerald-50 text-emerald-700",
  };
  return (
    <Badge
      variant="outline"
      className={cn(toneClass[scope])}
      data-testid="integrations-scope-badge"
      data-scope={scope}
    >
      {t(`api_keys.scope.${scope}`)}
    </Badge>
  );
}

function WebhookCard({
  testId,
  label,
  url,
  header,
  onCopy,
}: {
  testId: string;
  label: string;
  url: string;
  header: string;
  onCopy: (url: string) => void;
}) {
  const { t } = useTranslation("integrations");
  return (
    <div
      className="flex flex-col gap-2 rounded-md border bg-card p-4"
      data-testid={testId}
    >
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold">{label}</span>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => onCopy(url)}
          data-testid={`${testId}-copy`}
        >
          <Copy className="h-3 w-3" aria-hidden />
          <span>{t("webhooks.copy")}</span>
        </Button>
      </div>
      <code
        className="block break-all rounded bg-muted px-2 py-1 font-mono text-xs"
        data-testid={`${testId}-url`}
      >
        {url}
      </code>
      <span className="text-xs text-muted-foreground">{header}</span>
    </div>
  );
}

export function IntegrationsPage() {
  const { t, i18n } = useTranslation("integrations");
  const queryClient = useQueryClient();
  // L-18: mirror the backend's write rules instead of relying on 403s.
  // usePermissions resolves a null / not-yet-bootstrapped user to
  // `developer` (least privilege), so the gated actions stay hidden while
  // auth is still loading.
  const { isTeamAdminOrAbove } = usePermissions();
  const currentUserId = useAuthStore((s) => s.user?.id ?? null);

  const [page, setPage] = useState(1);
  const [createOpen, setCreateOpen] = useState(false);
  const [revealKey, setRevealKey] = useState<APIKeyCreateOut | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<APIKeyListItem | null>(null);
  const [toast, setToast] = useState<AdminToastMessage | null>(null);
  // Monotonic id sequence — kept in state so React's batched renders
  // observe a fresh value on every showToast() call.
  const [, setToastSeq] = useState(0);

  const params = { page, page_size: PAGE_SIZE };
  const keysQuery = useApiKeys(params);
  const items = keysQuery.data?.items ?? [];
  const total = keysQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  function showToast(text: string, tone: "success" | "error", key: string) {
    setToastSeq((n) => {
      const id = n + 1;
      setToast({ id, text, tone, key });
      return id;
    });
  }

  const createMutation = useMutation({
    mutationFn: (payload: APIKeyCreatePayload) => createApiKey(payload),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: ["api-keys"] });
      setCreateOpen(false);
      setRevealKey(created);
      showToast(t("api_keys.toast.created"), "success", "created");
    },
    onError: (err) => {
      const text =
        err instanceof ProblemError
          ? err.detail || t("api_keys.errors.create_failed")
          : t("api_keys.errors.create_failed");
      showToast(text, "error", "create_failed");
    },
  });

  const revokeMutation = useMutation({
    mutationFn: (id: string) => revokeApiKey(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["api-keys"] });
      setRevokeTarget(null);
      showToast(t("api_keys.toast.revoked"), "success", "revoked");
    },
    onError: (err) => {
      const text =
        err instanceof ProblemError
          ? err.detail || t("api_keys.errors.revoke_failed")
          : t("api_keys.errors.revoke_failed");
      showToast(text, "error", "revoke_failed");
    },
  });

  async function copyToClipboard(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      // Lightweight feedback via the same toast surface so users hear the
      // confirmation without waiting for a dialog state change.
      showToast(t("api_keys.create_result.copied"), "success", "copied");
    } catch {
      showToast(t("api_keys.errors.copy_failed"), "error", "copy_failed");
    }
  }

  const apiBase = getApiBase();
  const githubWebhookUrl = `${apiBase}/v1/webhooks/github`;
  const gitlabWebhookUrl = `${apiBase}/v1/webhooks/gitlab`;

  return (
    <div className="flex h-full flex-col" data-testid="integrations-page">
      <PageHeader
        title={
          <>
            <KeyRound className="h-4 w-4" aria-hidden />
            {t("page.title")}
          </>
        }
        titleProps={{ className: "flex items-center gap-2" }}
        description={t("page.subtitle")}
      />

      <div className="flex-1 space-y-8 overflow-y-auto px-6 py-6">
        {/* ---------- API keys section ----------------------------------- */}
        <section
          className="space-y-3"
          data-testid="integrations-api-keys-section"
        >
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold">
                {t("api_keys.section_title")}
              </h2>
              <p className="text-xs text-muted-foreground">
                {t("api_keys.section_description")}
              </p>
            </div>
            {/* Key creation is team_admin+ on the backend — hide the entry
                point for developers instead of letting them hit a 403. */}
            {isTeamAdminOrAbove ? (
              <Button
                type="button"
                size="sm"
                onClick={() => setCreateOpen(true)}
                data-testid="integrations-create-key"
              >
                <Plus className="h-3 w-3" aria-hidden />
                <span>{t("api_keys.create_button")}</span>
              </Button>
            ) : null}
          </div>

          {keysQuery.isError ? (
            <Alert variant="destructive" data-testid="integrations-keys-error">
              <AlertDescription>{t("api_keys.error")}</AlertDescription>
            </Alert>
          ) : null}

          <div className="overflow-x-auto rounded-md border">
            <table
              className="w-full min-w-[880px] text-sm"
              data-testid="integrations-keys-table"
              aria-busy={keysQuery.isLoading}
            >
              <thead className="bg-muted/40">
                <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
                  <th className="px-3 py-2">{t("api_keys.table.name")}</th>
                  <th className="px-3 py-2">{t("api_keys.table.scope")}</th>
                  <th className="px-3 py-2">{t("api_keys.table.prefix")}</th>
                  <th className="px-3 py-2">{t("api_keys.table.creator")}</th>
                  <th className="px-3 py-2">{t("api_keys.table.created")}</th>
                  <th className="px-3 py-2">{t("api_keys.table.last_used")}</th>
                  <th className="px-3 py-2">{t("api_keys.table.expires")}</th>
                  <th className="px-3 py-2">{t("api_keys.table.status")}</th>
                  <th className="px-3 py-2 text-right">
                    {t("api_keys.table.actions")}
                  </th>
                </tr>
              </thead>
              <tbody data-testid="integrations-keys-tbody">
                {keysQuery.isLoading
                  ? Array.from({ length: 4 }).map((_, i) => (
                      <tr key={`skeleton-${i}`} className="border-b">
                        <td className="px-3 py-2" colSpan={COLUMN_COUNT}>
                          <Skeleton className="h-5 w-full" />
                        </td>
                      </tr>
                    ))
                  : items.map((row) => {
                      const isRevoked = row.revoked_at !== null;
                      // L-18: revoke is "issuer or admin" on the backend.
                      // Developers only ever see the button on keys they
                      // issued themselves; team_admin+ see it on every row
                      // the list endpoint already deemed visible to them.
                      const canRevoke =
                        isTeamAdminOrAbove ||
                        (currentUserId !== null &&
                          row.created_by_user_id === currentUserId);
                      return (
                        <tr
                          key={row.id}
                          data-testid="integrations-key-row"
                          data-key-id={row.id}
                          data-key-prefix={row.key_prefix}
                          data-revoked={isRevoked}
                          className={cn(
                            "border-b transition-colors duration-fast ease-out-soft hover:bg-accent/40",
                            isRevoked && "opacity-60",
                          )}
                          style={{ height: "var(--table-row)" }}
                        >
                          <td className="max-w-[200px] truncate px-3">
                            <span className="font-medium">{row.name}</span>
                          </td>
                          <td className="px-3">
                            <ScopeBadge scope={row.scope} />
                          </td>
                          <td className="px-3 font-mono text-xs">
                            {row.key_prefix}…
                          </td>
                          <td
                            className="max-w-[180px] truncate px-3 text-xs text-muted-foreground"
                            data-testid="integrations-key-creator"
                            title={row.created_by_email ?? undefined}
                          >
                            {row.created_by_email ?? "—"}
                          </td>
                          <td className="px-3 text-xs text-muted-foreground">
                            <RelativeTime
                              value={row.created_at}
                              locale={i18n.resolvedLanguage}
                            />
                          </td>
                          <td
                            className="px-3 text-xs text-muted-foreground"
                            data-testid="integrations-key-last-used"
                          >
                            {row.last_used_at ? (
                              <RelativeTime
                                value={row.last_used_at}
                                locale={i18n.resolvedLanguage}
                              />
                            ) : (
                              t("api_keys.last_used_never")
                            )}
                          </td>
                          <td className="px-3 text-xs text-muted-foreground">
                            {row.expires_at ? (
                              <RelativeTime
                                value={row.expires_at}
                                locale={i18n.resolvedLanguage}
                              />
                            ) : (
                              t("api_keys.expires_never")
                            )}
                          </td>
                          <td className="px-3">
                            <StatusBadge revoked={isRevoked} />
                          </td>
                          <td className="px-3 text-right">
                            {!isRevoked && canRevoke ? (
                              <Button
                                type="button"
                                size="sm"
                                variant="outline"
                                onClick={() => setRevokeTarget(row)}
                                data-testid="integrations-key-revoke"
                                data-key-id={row.id}
                              >
                                <Trash2 className="h-3 w-3" aria-hidden />
                                <span>{t("api_keys.revoke")}</span>
                              </Button>
                            ) : null}
                          </td>
                        </tr>
                      );
                    })}
                {!keysQuery.isLoading && items.length === 0 ? (
                  <tr>
                    <td
                      colSpan={COLUMN_COUNT}
                      className="px-3 py-8 text-center text-sm text-muted-foreground"
                      data-testid="integrations-keys-empty"
                    >
                      {t("api_keys.empty")}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>

          {totalPages > 1 ? (
            <div
              className="flex items-center justify-between text-xs"
              data-testid="integrations-pagination"
            >
              <span className="text-muted-foreground">
                {t("api_keys.pagination.summary", {
                  page,
                  total: totalPages,
                })}
              </span>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  data-testid="integrations-page-prev"
                >
                  {t("api_keys.pagination.previous")}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  data-testid="integrations-page-next"
                >
                  {t("api_keys.pagination.next")}
                </Button>
              </div>
            </div>
          ) : null}
        </section>

        {/* ---------- Webhooks section ------------------------------------ */}
        <section
          className="space-y-3"
          data-testid="integrations-webhooks-section"
        >
          <div>
            <h2 className="text-base font-semibold">
              {t("webhooks.section_title")}
            </h2>
            <p className="text-xs text-muted-foreground">
              {t("webhooks.section_description")}
            </p>
          </div>

          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <WebhookCard
              testId="integrations-webhook-github"
              label={t("webhooks.github.label")}
              url={githubWebhookUrl}
              header={t("webhooks.github.header")}
              onCopy={(url) => void copyToClipboard(url)}
            />
            <WebhookCard
              testId="integrations-webhook-gitlab"
              label={t("webhooks.gitlab.label")}
              url={gitlabWebhookUrl}
              header={t("webhooks.gitlab.header")}
              onCopy={(url) => void copyToClipboard(url)}
            />
          </div>
        </section>
      </div>

      <CreateApiKeyDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onSubmit={(payload) => createMutation.mutate(payload)}
        submitting={createMutation.isPending}
      />

      <RevealApiKeyDialog
        created={revealKey}
        onClose={() => setRevealKey(null)}
        onCopy={(value) => void copyToClipboard(value)}
      />

      <RevokeApiKeyDialog
        target={revokeTarget}
        onClose={() => setRevokeTarget(null)}
        onConfirm={(id) => revokeMutation.mutate(id)}
        submitting={revokeMutation.isPending}
      />

      <AdminToast message={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}
