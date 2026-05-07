/**
 * AdminUserDrawer — right-slide Sheet detail view for an admin user.
 *
 * Sections:
 *   - Header: email, role badge, active/inactive badge.
 *   - Meta: created_at, last_login_at, scan_count.
 *   - Memberships: per-team role list.
 *   - Actions: change role (inline form), deactivate/activate, reset password.
 *
 * Confirmation pattern: the destructive action uses a small inline
 * "Are you sure?" prompt below the row instead of a dialog. This avoids
 * pulling in shadcn `AlertDialog` (not present) for one of the few times
 * the user needs it.
 *
 * Errors translate via `adminErrorMessageKey` so domain invariants
 * (last_super_admin_protected, cannot_modify_self) surface in the toast.
 */
import { Loader2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { useAdminUser } from "@/features/admin/api/useAdminUser";
import {
  useActivateUser,
  useDeactivateUser,
  useResetUserPassword,
  useUpdateUserRole,
} from "@/features/admin/api/useAdminUserMutations";
import type { UserRole } from "@/features/admin/api/adminUsersApi";
import { RoleBadge } from "@/features/admin/components/RoleBadge";
import { adminErrorMessageKey } from "@/features/admin/lib/adminErrorMessage";
import { formatRelativeToNow } from "@/lib/relativeTime";
import { cn } from "@/lib/utils";

interface AdminUserDrawerProps {
  open: boolean;
  userId: string | null;
  onOpenChange: (open: boolean) => void;
  /**
   * Surface a tone-aware message to the parent (toast surface).
   */
  notify: (text: string, tone: "success" | "error") => void;
}

type ConfirmKind = "deactivate" | "activate" | "reset_password" | null;

export function AdminUserDrawer({
  open,
  userId,
  onOpenChange,
  notify,
}: AdminUserDrawerProps) {
  const { t, i18n } = useTranslation("admin");
  const detail = useAdminUser(open ? userId : null);

  const [showRoleForm, setShowRoleForm] = useState(false);
  const [roleSelection, setRoleSelection] = useState<UserRole>("developer");
  const [teamIdInput, setTeamIdInput] = useState("");
  const [confirm, setConfirm] = useState<ConfirmKind>(null);

  const updateRole = useUpdateUserRole();
  const deactivate = useDeactivateUser();
  const activate = useActivateUser();
  const reset = useResetUserPassword();

  // Reset transient form state whenever the loaded user changes.
  const detailId = detail.data?.id;
  useEffect(() => {
    setShowRoleForm(false);
    setConfirm(null);
    setTeamIdInput("");
    if (detail.data) {
      const fallbackRole: UserRole = detail.data.is_superuser
        ? "super_admin"
        : detail.data.memberships[0]?.role ?? "developer";
      setRoleSelection(fallbackRole);
    }
  }, [detailId, detail.data]);

  const lastLoginRel = useMemo(() => {
    return detail.data?.last_login_at
      ? formatRelativeToNow(detail.data.last_login_at, i18n.resolvedLanguage)
      : t("admin.users.drawer.never");
  }, [detail.data?.last_login_at, i18n.resolvedLanguage, t]);

  const createdRel = useMemo(() => {
    return detail.data?.created_at
      ? formatRelativeToNow(detail.data.created_at, i18n.resolvedLanguage)
      : "—";
  }, [detail.data?.created_at, i18n.resolvedLanguage]);

  async function handleSaveRole() {
    if (!detail.data) return;
    try {
      await updateRole.mutateAsync({
        userId: detail.data.id,
        payload: {
          role: roleSelection,
          team_id:
            roleSelection === "super_admin"
              ? null
              : (teamIdInput.trim() || null),
        },
      });
      setShowRoleForm(false);
      notify(t("admin.users.toast.role_updated"), "success");
    } catch (err) {
      notify(t(adminErrorMessageKey(err)), "error");
    }
  }

  async function handleDeactivate() {
    if (!detail.data) return;
    try {
      await deactivate.mutateAsync({ userId: detail.data.id });
      notify(t("admin.users.toast.deactivated"), "success");
      setConfirm(null);
    } catch (err) {
      notify(t(adminErrorMessageKey(err)), "error");
    }
  }

  async function handleActivate() {
    if (!detail.data) return;
    try {
      await activate.mutateAsync({ userId: detail.data.id });
      notify(t("admin.users.toast.activated"), "success");
      setConfirm(null);
    } catch (err) {
      notify(t(adminErrorMessageKey(err)), "error");
    }
  }

  async function handleReset() {
    if (!detail.data) return;
    try {
      await reset.mutateAsync({ userId: detail.data.id });
      notify(t("admin.users.toast.password_reset_sent"), "success");
      setConfirm(null);
    } catch (err) {
      notify(t(adminErrorMessageKey(err)), "error");
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="flex w-full max-w-lg flex-col gap-4 overflow-y-auto sm:max-w-lg"
        data-testid="admin-user-drawer"
      >
        <SheetHeader>
          <SheetTitle>
            {detail.data?.full_name || detail.data?.email || t("admin.users.title")}
          </SheetTitle>
          <SheetDescription className="font-mono text-xs">
            {detail.data?.email ?? ""}
          </SheetDescription>
        </SheetHeader>

        {detail.isLoading ? (
          <div className="space-y-2" data-testid="admin-user-drawer-loading">
            <Skeleton className="h-6 w-1/2" />
            <Skeleton className="h-6 w-1/3" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : null}

        {detail.isError ? (
          <Alert variant="destructive">
            <AlertDescription>{t("admin.errors.unknown")}</AlertDescription>
          </Alert>
        ) : null}

        {detail.data ? (
          <>
            <section
              className="flex flex-wrap items-center gap-2"
              aria-label={t("admin.users.column.role")}
            >
              <RoleBadge
                role={
                  detail.data.is_superuser
                    ? "super_admin"
                    : detail.data.memberships[0]?.role ?? "developer"
                }
              />
              <Badge
                variant="outline"
                className={cn(
                  detail.data.is_active
                    ? "border-emerald-300 bg-emerald-50 text-emerald-700"
                    : "border-muted bg-muted text-muted-foreground",
                )}
                data-testid="user-active-badge"
                data-active={detail.data.is_active}
              >
                {detail.data.is_active
                  ? t("admin.users.status.active")
                  : t("admin.users.status.inactive")}
              </Badge>
            </section>

            <section className="grid grid-cols-2 gap-3 text-xs">
              <Meta label={t("admin.users.drawer.created_at")} value={createdRel} />
              <Meta
                label={t("admin.users.drawer.last_login_at")}
                value={lastLoginRel}
              />
              <Meta
                label={t("admin.users.drawer.scan_count")}
                value={String(detail.data.scan_count)}
              />
            </section>

            <section
              className="space-y-2"
              data-testid="admin-user-memberships"
            >
              <h3 className="text-sm font-semibold">
                {t("admin.users.drawer.memberships")}
              </h3>
              {detail.data.memberships.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  {t("admin.users.drawer.no_memberships")}
                </p>
              ) : (
                <ul className="divide-y rounded-md border text-sm">
                  {detail.data.memberships.map((m) => (
                    <li
                      key={m.team_id}
                      className="flex items-center justify-between px-3 py-2"
                      data-testid="admin-user-membership-row"
                    >
                      <span className="truncate">{m.team_name}</span>
                      <Badge
                        variant="outline"
                        className="bg-muted text-muted-foreground"
                      >
                        {t(`admin.users.role.${m.role}`)}
                      </Badge>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section
              className="flex flex-wrap items-center gap-2 border-t pt-4"
              data-testid="admin-user-actions"
            >
              <Button
                size="sm"
                variant="outline"
                onClick={() => setShowRoleForm((s) => !s)}
                data-testid="admin-user-action-change-role"
              >
                {t("admin.users.action.change_role")}
              </Button>
              {detail.data.is_active ? (
                <Button
                  size="sm"
                  variant="destructive"
                  onClick={() => setConfirm("deactivate")}
                  data-testid="admin-user-action-deactivate"
                >
                  {t("admin.users.action.deactivate")}
                </Button>
              ) : (
                <Button
                  size="sm"
                  variant="default"
                  onClick={() => setConfirm("activate")}
                  data-testid="admin-user-action-activate"
                >
                  {t("admin.users.action.activate")}
                </Button>
              )}
              <Button
                size="sm"
                variant="outline"
                onClick={() => setConfirm("reset_password")}
                data-testid="admin-user-action-reset"
              >
                {t("admin.users.action.reset_password")}
              </Button>
            </section>

            {showRoleForm ? (
              <section
                className="space-y-2 rounded-md border bg-muted/20 p-3"
                data-testid="admin-user-role-form"
              >
                <div className="grid gap-2 sm:grid-cols-2">
                  <div>
                    <Label
                      htmlFor="admin-user-role-select"
                      className="text-xs text-muted-foreground"
                    >
                      {t("admin.users.form.role_label")}
                    </Label>
                    <select
                      id="admin-user-role-select"
                      data-testid="admin-user-role-select"
                      className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm"
                      value={roleSelection}
                      onChange={(e) =>
                        setRoleSelection(e.target.value as UserRole)
                      }
                    >
                      <option value="super_admin">
                        {t("admin.users.role.super_admin")}
                      </option>
                      <option value="team_admin">
                        {t("admin.users.role.team_admin")}
                      </option>
                      <option value="developer">
                        {t("admin.users.role.developer")}
                      </option>
                    </select>
                  </div>
                  {roleSelection !== "super_admin" ? (
                    <div>
                      <Label
                        htmlFor="admin-user-team-id"
                        className="text-xs text-muted-foreground"
                      >
                        {t("admin.users.form.team_label")}
                      </Label>
                      <Input
                        id="admin-user-team-id"
                        data-testid="admin-user-team-id"
                        value={teamIdInput}
                        placeholder={t("admin.users.form.team_id_placeholder")}
                        onChange={(e) => setTeamIdInput(e.target.value)}
                        className="h-9 font-mono text-xs"
                      />
                    </div>
                  ) : null}
                </div>
                <div className="flex justify-end gap-2">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setShowRoleForm(false)}
                    data-testid="admin-user-role-cancel"
                  >
                    {t("admin.actions.cancel")}
                  </Button>
                  <Button
                    size="sm"
                    onClick={handleSaveRole}
                    disabled={updateRole.isPending}
                    data-testid="admin-user-role-save"
                  >
                    {updateRole.isPending ? (
                      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                    ) : null}
                    {t("admin.users.form.save")}
                  </Button>
                </div>
              </section>
            ) : null}

            {confirm ? (
              <ConfirmStrip
                kind={confirm}
                onCancel={() => setConfirm(null)}
                onConfirm={
                  confirm === "deactivate"
                    ? handleDeactivate
                    : confirm === "activate"
                      ? handleActivate
                      : handleReset
                }
                isPending={
                  (confirm === "deactivate" && deactivate.isPending) ||
                  (confirm === "activate" && activate.isPending) ||
                  (confirm === "reset_password" && reset.isPending)
                }
              />
            ) : null}
          </>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}

interface MetaProps {
  label: string;
  value: string;
}

function Meta({ label, value }: MetaProps) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="text-sm">{value}</div>
    </div>
  );
}

interface ConfirmStripProps {
  kind: "deactivate" | "activate" | "reset_password";
  onCancel: () => void;
  onConfirm: () => void;
  isPending: boolean;
}

function ConfirmStrip({ kind, onCancel, onConfirm, isPending }: ConfirmStripProps) {
  const { t } = useTranslation("admin");
  return (
    <div
      className="flex flex-col gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
      data-testid="admin-user-confirm-strip"
      data-kind={kind}
    >
      <p>{t(`admin.users.confirm.${kind}`)}</p>
      <div className="flex justify-end gap-2">
        <Button
          size="sm"
          variant="ghost"
          onClick={onCancel}
          data-testid="admin-user-confirm-cancel"
        >
          {t("admin.actions.cancel")}
        </Button>
        <Button
          size="sm"
          variant="default"
          onClick={onConfirm}
          disabled={isPending}
          data-testid="admin-user-confirm-ok"
        >
          {isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          ) : null}
          {t("admin.actions.confirm")}
        </Button>
      </div>
    </div>
  );
}
