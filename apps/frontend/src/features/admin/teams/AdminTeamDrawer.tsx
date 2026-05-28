/**
 * AdminTeamDrawer — right-slide Sheet detail view for an admin team.
 *
 * Sections:
 *   - Header: name, slug (mono), member count, project count.
 *   - Edit form: name / slug / description (toggle).
 *   - Add-member form: user_id + role select (inline).
 *   - Members table: per-row remove with inline confirm.
 *   - Delete team button: inline confirm + propagates Problem extension
 *     `team_has_active_scans` into the toast.
 */
import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
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
import {
  useAddTeamMember,
  useDeleteTeam,
  useRemoveTeamMember,
  useUpdateTeam,
} from "@/features/admin/api/useAdminTeamMutations";
import { useAdminTeam } from "@/features/admin/api/useAdminTeams";
import type { TeamMembershipRole } from "@/features/admin/api/adminUsersApi";
import {
  adminErrorExtension,
  adminErrorMessageKey,
} from "@/features/admin/lib/adminErrorMessage";
import { cn } from "@/lib/utils";

interface AdminTeamDrawerProps {
  open: boolean;
  teamId: string | null;
  onOpenChange: (open: boolean) => void;
  notify: (text: string, tone: "success" | "error", key?: string) => void;
  /**
   * Called after a successful delete so the parent page can clear its
   * selection and let the list query re-render.
   */
  onDeleted?: () => void;
}

type ConfirmKind =
  | { kind: "delete_team" }
  | { kind: "remove_member"; userId: string; email: string }
  | null;

export function AdminTeamDrawer({
  open,
  teamId,
  onOpenChange,
  notify,
  onDeleted,
}: AdminTeamDrawerProps) {
  const { t } = useTranslation("admin");
  const detail = useAdminTeam(open ? teamId : null);

  const [showEditForm, setShowEditForm] = useState(false);
  const [editName, setEditName] = useState("");
  const [editSlug, setEditSlug] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [showAddMember, setShowAddMember] = useState(false);
  const [memberUserId, setMemberUserId] = useState("");
  const [memberRole, setMemberRole] = useState<TeamMembershipRole>("developer");
  const [confirm, setConfirm] = useState<ConfirmKind>(null);

  const update = useUpdateTeam();
  const del = useDeleteTeam();
  const add = useAddTeamMember();
  const remove = useRemoveTeamMember();

  // Re-seed form fields when the loaded team changes.
  const detailId = detail.data?.id;
  useEffect(() => {
    setShowEditForm(false);
    setShowAddMember(false);
    setConfirm(null);
    setMemberUserId("");
    setMemberRole("developer");
    if (detail.data) {
      setEditName(detail.data.name);
      setEditSlug(detail.data.slug);
      setEditDescription(detail.data.description ?? "");
    }
  }, [detailId, detail.data]);

  async function handleSaveTeam() {
    if (!detail.data) return;
    try {
      await update.mutateAsync({
        teamId: detail.data.id,
        payload: {
          name: editName.trim() || undefined,
          slug: editSlug.trim() || undefined,
          description: editDescription.trim() ? editDescription.trim() : null,
        },
      });
      setShowEditForm(false);
      notify(t("admin.teams.toast.updated"), "success", "updated");
    } catch (err) {
      notify(t(adminErrorMessageKey(err)), "error", adminErrorExtension(err));
    }
  }

  async function handleDeleteTeam() {
    if (!detail.data) return;
    try {
      await del.mutateAsync({ teamId: detail.data.id });
      notify(t("admin.teams.toast.deleted"), "success", "deleted");
      setConfirm(null);
      onDeleted?.();
      onOpenChange(false);
    } catch (err) {
      notify(t(adminErrorMessageKey(err)), "error", adminErrorExtension(err));
    }
  }

  async function handleAddMember() {
    if (!detail.data) return;
    if (!memberUserId.trim()) return;
    try {
      await add.mutateAsync({
        teamId: detail.data.id,
        payload: {
          user_id: memberUserId.trim(),
          role: memberRole,
        },
      });
      setShowAddMember(false);
      setMemberUserId("");
      notify(t("admin.teams.toast.member_added"), "success", "member_added");
    } catch (err) {
      notify(t(adminErrorMessageKey(err)), "error", adminErrorExtension(err));
    }
  }

  async function handleRemoveMember(userId: string) {
    if (!detail.data) return;
    try {
      await remove.mutateAsync({ teamId: detail.data.id, userId });
      notify(t("admin.teams.toast.member_removed"), "success", "member_removed");
      setConfirm(null);
    } catch (err) {
      notify(t(adminErrorMessageKey(err)), "error", adminErrorExtension(err));
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="flex w-full max-w-lg flex-col gap-4 overflow-y-auto sm:max-w-lg"
        data-testid="admin-team-drawer"
      >
        <SheetHeader>
          <SheetTitle>
            {detail.data?.name || t("admin.teams.title")}
          </SheetTitle>
          <SheetDescription className="font-mono text-xs">
            {detail.data?.slug ?? ""}
          </SheetDescription>
        </SheetHeader>

        {detail.isLoading ? (
          <div className="space-y-2" data-testid="admin-team-drawer-loading">
            <Skeleton className="h-6 w-1/2" />
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
            <section className="grid grid-cols-2 gap-3 text-xs">
              <Meta
                label={t("admin.teams.column.member_count")}
                value={String(detail.data.members.length)}
              />
              <Meta
                label={t("admin.teams.drawer.project_count")}
                value={String(detail.data.project_count)}
              />
            </section>

            {detail.data.description ? (
              <p className="text-sm text-muted-foreground">
                {detail.data.description}
              </p>
            ) : null}

            <section
              className="flex flex-wrap items-center gap-2"
              data-testid="admin-team-actions"
            >
              <Button
                size="sm"
                variant="outline"
                onClick={() => setShowEditForm((s) => !s)}
                data-testid="admin-team-action-edit"
              >
                {t("admin.teams.action.edit")}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setShowAddMember((s) => !s)}
                data-testid="admin-team-action-add-member"
              >
                {t("admin.teams.action.add_member")}
              </Button>
              <Button
                size="sm"
                variant="destructive"
                onClick={() => setConfirm({ kind: "delete_team" })}
                data-testid="admin-team-action-delete"
              >
                {t("admin.teams.action.delete")}
              </Button>
            </section>

            {showEditForm ? (
              <section
                className="space-y-2 rounded-md border bg-muted/20 p-3"
                data-testid="admin-team-edit-form"
              >
                <div>
                  <Label htmlFor="admin-team-name" className="text-xs">
                    {t("admin.teams.form.name_label")}
                  </Label>
                  <Input
                    id="admin-team-name"
                    data-testid="admin-team-name"
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    className="h-9"
                  />
                </div>
                <div>
                  <Label htmlFor="admin-team-slug" className="text-xs">
                    {t("admin.teams.form.slug_label")}
                  </Label>
                  <Input
                    id="admin-team-slug"
                    data-testid="admin-team-slug"
                    value={editSlug}
                    onChange={(e) => setEditSlug(e.target.value)}
                    className="h-9 font-mono text-xs"
                  />
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    {t("admin.teams.form.slug_help")}
                  </p>
                </div>
                <div>
                  <Label htmlFor="admin-team-description" className="text-xs">
                    {t("admin.teams.form.description_label")}
                  </Label>
                  <textarea
                    id="admin-team-description"
                    data-testid="admin-team-description"
                    value={editDescription}
                    onChange={(e) => setEditDescription(e.target.value)}
                    rows={2}
                    className="flex w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  />
                </div>
                <div className="flex justify-end gap-2">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setShowEditForm(false)}
                  >
                    {t("admin.teams.form.cancel")}
                  </Button>
                  <Button
                    size="sm"
                    onClick={handleSaveTeam}
                    disabled={update.isPending}
                    data-testid="admin-team-edit-save"
                  >
                    {update.isPending ? (
                      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                    ) : null}
                    {t("admin.teams.form.save")}
                  </Button>
                </div>
              </section>
            ) : null}

            {showAddMember ? (
              <section
                className="space-y-2 rounded-md border bg-muted/20 p-3"
                data-testid="admin-team-add-member-form"
              >
                <div className="grid gap-2 sm:grid-cols-2">
                  <div>
                    <Label htmlFor="admin-team-member-user" className="text-xs">
                      {t("admin.teams.form.user_id_label")}
                    </Label>
                    <Input
                      id="admin-team-member-user"
                      data-testid="admin-team-member-user"
                      value={memberUserId}
                      onChange={(e) => setMemberUserId(e.target.value)}
                      className="h-9 font-mono text-xs"
                    />
                  </div>
                  <div>
                    <Label htmlFor="admin-team-member-role" className="text-xs">
                      {t("admin.teams.form.role_label")}
                    </Label>
                    <select
                      id="admin-team-member-role"
                      data-testid="admin-team-member-role"
                      value={memberRole}
                      onChange={(e) =>
                        setMemberRole(e.target.value as TeamMembershipRole)
                      }
                      className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm"
                    >
                      <option value="team_admin">
                        {t("admin.users.role.team_admin")}
                      </option>
                      <option value="developer">
                        {t("admin.users.role.developer")}
                      </option>
                    </select>
                  </div>
                </div>
                <div className="flex justify-end gap-2">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setShowAddMember(false)}
                  >
                    {t("admin.teams.form.cancel")}
                  </Button>
                  <Button
                    size="sm"
                    onClick={handleAddMember}
                    disabled={add.isPending || !memberUserId.trim()}
                    data-testid="admin-team-member-add-save"
                  >
                    {add.isPending ? (
                      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                    ) : null}
                    {t("admin.teams.form.add")}
                  </Button>
                </div>
              </section>
            ) : null}

            <section className="space-y-2" data-testid="admin-team-members">
              <h3 className="text-sm font-semibold">
                {t("admin.teams.drawer.members")}
              </h3>
              {detail.data.members.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  {t("admin.teams.drawer.no_members")}
                </p>
              ) : (
                <ul className="divide-y rounded-md border">
                  {detail.data.members.map((m) => {
                    const isPending =
                      confirm?.kind === "remove_member" &&
                      confirm.userId === m.user_id;
                    return (
                      <li
                        key={m.user_id}
                        className={cn(
                          "flex flex-col gap-1 px-3 py-2 text-sm",
                          isPending && "bg-amber-50",
                        )}
                        data-testid="admin-team-member-row"
                        data-user-id={m.user_id}
                        data-email={m.email}
                        data-role={m.role}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <div className="min-w-0">
                            <div className="truncate">
                              {m.full_name || m.email}
                            </div>
                            <div className="truncate font-mono text-xs text-muted-foreground">
                              {m.email}
                            </div>
                          </div>
                          <div className="flex items-center gap-2">
                            <Badge
                              variant="outline"
                              className="bg-muted text-muted-foreground"
                            >
                              {t(`admin.users.role.${m.role}`)}
                            </Badge>
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() =>
                                setConfirm({
                                  kind: "remove_member",
                                  userId: m.user_id,
                                  email: m.email,
                                })
                              }
                              data-testid="admin-team-member-remove"
                            >
                              {t("admin.teams.action.remove_member")}
                            </Button>
                          </div>
                        </div>
                        {isPending ? (
                          <div
                            className="flex items-center justify-end gap-2"
                            data-testid="admin-team-member-confirm-strip"
                          >
                            <span className="mr-auto text-xs text-amber-900">
                              {t("admin.teams.confirm.remove_member")}
                            </span>
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => setConfirm(null)}
                            >
                              {t("admin.actions.cancel")}
                            </Button>
                            <Button
                              size="sm"
                              onClick={() => handleRemoveMember(m.user_id)}
                              disabled={remove.isPending}
                              data-testid="admin-team-member-confirm-ok"
                            >
                              {remove.isPending ? (
                                <Loader2
                                  className="h-4 w-4 animate-spin"
                                  aria-hidden
                                />
                              ) : null}
                              {t("admin.actions.confirm")}
                            </Button>
                          </div>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>

            {confirm?.kind === "delete_team" ? (
              <div
                className="flex flex-col gap-2 rounded-md border border-destructive/50 bg-destructive/5 px-3 py-2 text-sm"
                data-testid="admin-team-delete-confirm"
              >
                <p>{t("admin.teams.confirm.delete")}</p>
                <div className="flex justify-end gap-2">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setConfirm(null)}
                  >
                    {t("admin.actions.cancel")}
                  </Button>
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={handleDeleteTeam}
                    disabled={del.isPending}
                    data-testid="admin-team-delete-confirm-ok"
                  >
                    {del.isPending ? (
                      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                    ) : null}
                    {t("admin.actions.confirm")}
                  </Button>
                </div>
              </div>
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
