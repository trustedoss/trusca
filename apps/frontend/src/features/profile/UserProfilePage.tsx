/**
 * UserProfilePage — chore G ("Connected Accounts" UI).
 *
 * The first profile-management surface in the portal. Today it carries one
 * content section (Connected Accounts); the layout is intentionally extensible
 * so future sections — change-password, change-email, MFA, API tokens — can
 * stack underneath without restructuring the page.
 *
 * Sections:
 *   1. Page header — display name + email pulled from the in-memory auth
 *      store. No profile-edit form yet (future chore).
 *   2. Connected Accounts — lists OAuth providers linked to the caller and
 *      offers an inline-confirmation Unlink action per row. The 409 case
 *      ``urn:trustedoss:problem:oauth_unlink_blocks_login`` does NOT toast;
 *      it surfaces in-place as a red banner above the row so the user reads
 *      the actionable copy ("Set a password before unlinking.") instead of
 *      a transient notification.
 *
 * Patterns reused:
 *   - Inline confirmation strip from `AdminUserDrawer` (no AlertDialog).
 *   - `AdminToast` for success / generic error feedback (matches /integrations).
 *   - Compact 40px row density consistent with Integrations / Admin tables.
 *
 * No hardcoded English in JSX — every visible string flows through `t()`
 * under the `profile` and `common` namespaces.
 */
import { Loader2, Trash2, UserCircle2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { ProviderIcon } from "@/components/ProviderIcon";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AdminToast,
  type AdminToastMessage,
} from "@/features/admin/components/AdminToast";
import {
  OAUTH_UNLINK_BLOCKS_LOGIN_TYPE,
  type OAuthIdentity,
  type OAuthProvider,
} from "@/features/profile/api/oauthIdentitiesApi";
import {
  useOAuthIdentities,
  useUnlinkIdentity,
} from "@/features/profile/useOAuthIdentities";
import { ProblemError } from "@/lib/problem";
import { formatRelativeToNow } from "@/lib/relativeTime";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/stores/authStore";

/** RFC 7807 problem-type detector — kept here so callers don't import the
 *  axios layer directly. */
function isUnlinkBlocksLogin(err: unknown): boolean {
  if (!(err instanceof ProblemError)) return false;
  return err.problem?.type === OAUTH_UNLINK_BLOCKS_LOGIN_TYPE;
}

interface IdentityRowProps {
  identity: OAuthIdentity;
  /** Locale tag for relative-time formatting. */
  locale?: string;
  /** True when the inline confirm strip is the active row. */
  confirming: boolean;
  /** True when the blocks-login alert is active for this row. */
  showBlocksLogin: boolean;
  /**
   * M-16: true when this is the caller's ONLY identity and no password is
   * set — unlinking would lock them out, so the button is pre-disabled with
   * an explanatory tooltip instead of letting the server 409 after the click.
   */
  unlinkLocked: boolean;
  onAskUnlink: () => void;
  onCancelUnlink: () => void;
  onConfirmUnlink: () => void;
  isPending: boolean;
}

function IdentityRow({
  identity,
  locale,
  confirming,
  showBlocksLogin,
  unlinkLocked,
  onAskUnlink,
  onCancelUnlink,
  onConfirmUnlink,
  isPending,
}: IdentityRowProps) {
  const { t } = useTranslation("profile");
  const providerLabel = t(
    `connected_accounts.provider.${identity.provider}` as const,
  );
  const linkedRel = formatRelativeToNow(identity.created_at, locale);

  return (
    <div
      className="flex flex-col gap-2 border-b last:border-b-0"
      data-testid="profile-identity-row"
      data-identity-id={identity.id}
      data-provider={identity.provider}
    >
      {showBlocksLogin ? (
        <Alert
          variant="destructive"
          className="my-2"
          data-testid="profile-unlink-blocks-login"
        >
          <AlertDescription>{t("unlink.blocks_login_alert")}</AlertDescription>
        </Alert>
      ) : null}

      <div
        className="flex items-center gap-3 px-3"
        style={{ minHeight: "var(--table-row)" }}
      >
        <span
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-muted text-foreground"
          aria-hidden
        >
          <ProviderIcon provider={identity.provider as OAuthProvider} />
        </span>
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="text-sm font-medium" data-testid="profile-identity-provider">
            {providerLabel}
          </span>
          <span
            className="truncate text-xs text-muted-foreground"
            data-testid="profile-identity-email"
            title={identity.provider_email ?? undefined}
          >
            {identity.provider_email ?? t("connected_accounts.no_email")}
          </span>
        </div>
        <span
          className="hidden text-xs text-muted-foreground sm:inline"
          data-testid="profile-identity-linked"
          title={identity.created_at}
        >
          {t("connected_accounts.linked_since", { relative: linkedRel })}
        </span>
        {/* M-16: the wrapping span carries the tooltip because the shadcn
            Button applies `disabled:pointer-events-none`, which suppresses
            a `title` set on the (disabled) button itself. */}
        <span
          title={unlinkLocked ? t("unlink.blocks_login_alert") : undefined}
          data-testid="profile-identity-unlink-wrap"
        >
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={onAskUnlink}
            disabled={confirming || isPending || unlinkLocked}
            title={unlinkLocked ? t("unlink.blocks_login_alert") : undefined}
            data-testid="profile-identity-unlink"
          >
            <Trash2 className="h-3 w-3" aria-hidden />
            <span>{t("connected_accounts.unlink.button")}</span>
          </Button>
        </span>
      </div>

      {confirming ? (
        <div
          className="mx-3 mb-3 flex flex-col gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
          data-testid="profile-identity-confirm-strip"
        >
          <div>
            <p className="font-medium">
              {t("connected_accounts.unlink.confirm_title")}
            </p>
            <p className="text-xs">
              {t("connected_accounts.unlink.confirm_body")}
            </p>
          </div>
          <div className="flex justify-end gap-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={onCancelUnlink}
              data-testid="profile-identity-confirm-cancel"
            >
              {t("connected_accounts.unlink.cancel")}
            </Button>
            <Button
              size="sm"
              variant="destructive"
              onClick={onConfirmUnlink}
              disabled={isPending}
              data-testid="profile-identity-confirm-ok"
            >
              {isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              ) : null}
              {t("connected_accounts.unlink.confirm")}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function UserProfilePage() {
  const { t, i18n } = useTranslation("profile");
  const { t: tCommon } = useTranslation();
  const user = useAuthStore((s) => s.user);

  const identitiesQuery = useOAuthIdentities();
  const unlink = useUnlinkIdentity();

  // Per-row UI state. Confirmation and blocks-login alert are scoped to a
  // single identity id at a time; opening Unlink on a different row clears
  // the previous prompt.
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [blocksLoginId, setBlocksLoginId] = useState<string | null>(null);

  const [toast, setToast] = useState<AdminToastMessage | null>(null);
  const [, setToastSeq] = useState(0);

  function showToast(text: string, tone: "success" | "error", key: string) {
    setToastSeq((n) => {
      const id = n + 1;
      setToast({ id, text, tone, key });
      return id;
    });
  }

  function askUnlink(id: string) {
    setConfirmingId(id);
    // Clearing the alert on a fresh attempt is intentional — the user may
    // have set a password since the last 409 and is retrying.
    setBlocksLoginId(null);
  }

  function cancelUnlink() {
    setConfirmingId(null);
  }

  async function confirmUnlink(id: string) {
    try {
      await unlink.mutateAsync(id);
      setConfirmingId(null);
      setBlocksLoginId(null);
      showToast(
        t("connected_accounts.toast.unlinked"),
        "success",
        "unlinked",
      );
    } catch (err) {
      if (isUnlinkBlocksLogin(err)) {
        // Surface in-place: the actionable copy ("set a password before
        // unlinking") matters more than a transient toast and the row
        // must stay so the user can act on it.
        setBlocksLoginId(id);
        setConfirmingId(null);
      } else {
        const text =
          err instanceof ProblemError && err.detail
            ? err.detail
            : t("connected_accounts.toast.unlink_failed");
        showToast(text, "error", "unlink_failed");
        setConfirmingId(null);
      }
    }
  }

  const items = identitiesQuery.data?.items ?? [];
  // M-16: pre-disable Unlink on the last identity of an OAuth-only account.
  // Defaults to `true` (NOT locked) while loading — the list isn't rendered
  // then anyway, and the server 409 remains the backstop for races.
  const hasPassword = identitiesQuery.data?.has_password ?? true;
  const unlinkLocked = items.length === 1 && !hasPassword;

  return (
    <div className="flex h-full flex-col" data-testid="user-profile-page">
      <header className="border-b bg-card px-6 py-4">
        <h1 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
          <UserCircle2 className="h-4 w-4" aria-hidden />
          {t("page.title")}
        </h1>
        <p className="text-sm text-muted-foreground">{t("page.subtitle")}</p>
      </header>

      <div className="flex-1 space-y-8 overflow-y-auto px-6 py-6">
        {/* ---------- Account header ----------------------------------- */}
        <section
          className="grid grid-cols-1 gap-4 rounded-md border bg-card p-4 sm:grid-cols-2"
          data-testid="profile-account-header"
          aria-label={tCommon("auth.profile")}
        >
          <div>
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
              {t("header.display_name_label")}
            </div>
            <div className="text-sm font-medium" data-testid="profile-display-name">
              {user?.displayName ?? "—"}
            </div>
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
              {t("header.email_label")}
            </div>
            <div
              className="font-mono text-xs text-muted-foreground"
              data-testid="profile-email"
            >
              {user?.email ?? "—"}
            </div>
          </div>
        </section>

        {/* ---------- Connected Accounts ------------------------------- */}
        <section
          className="space-y-3"
          data-testid="profile-connected-accounts"
        >
          <div>
            <h2 className="text-base font-semibold">
              {t("connected_accounts.section_title")}
            </h2>
            <p className="text-xs text-muted-foreground">
              {t("connected_accounts.section_description")}
            </p>
          </div>

          {identitiesQuery.isError ? (
            <Alert
              variant="destructive"
              data-testid="profile-identities-error"
            >
              <AlertDescription>
                {t("connected_accounts.error")}
              </AlertDescription>
            </Alert>
          ) : null}

          <div
            className={cn("overflow-hidden rounded-md border bg-card")}
            data-testid="profile-identities-list"
            aria-busy={identitiesQuery.isLoading}
          >
            {identitiesQuery.isLoading ? (
              <div className="space-y-2 p-3" data-testid="profile-identities-loading">
                {Array.from({ length: 2 }).map((_, i) => (
                  <Skeleton key={i} className="h-10 w-full" />
                ))}
              </div>
            ) : items.length === 0 ? (
              <div
                className="px-3 py-8 text-center text-sm text-muted-foreground"
                data-testid="profile-identities-empty"
              >
                {t("connected_accounts.empty")}
              </div>
            ) : (
              items.map((identity) => (
                <IdentityRow
                  key={identity.id}
                  identity={identity}
                  locale={i18n.resolvedLanguage}
                  confirming={confirmingId === identity.id}
                  showBlocksLogin={blocksLoginId === identity.id}
                  unlinkLocked={unlinkLocked}
                  onAskUnlink={() => askUnlink(identity.id)}
                  onCancelUnlink={cancelUnlink}
                  onConfirmUnlink={() => void confirmUnlink(identity.id)}
                  isPending={
                    unlink.isPending &&
                    (unlink.variables === identity.id ||
                      confirmingId === identity.id)
                  }
                />
              ))
            )}
          </div>
        </section>
      </div>

      <AdminToast message={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}
