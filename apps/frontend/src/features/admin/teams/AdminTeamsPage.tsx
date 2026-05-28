/**
 * AdminTeamsPage — Phase 4 PR #13 §4.3.
 *
 * Same skeleton as AdminUsersPage:
 *   - Inline toolbar (search + "New team" button).
 *   - Compact table (40px rows): name, slug, description, member_count,
 *     project_count.
 *   - Right drawer for detail.
 *
 * "New team" expands an inline create form above the table. The form
 * collapses on success and a toast confirms.
 */
import { Loader2, Plus } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useCreateTeam } from "@/features/admin/api/useAdminTeamMutations";
import { useAdminTeams } from "@/features/admin/api/useAdminTeams";
import { AdminToast, type AdminToastMessage } from "@/features/admin/components/AdminToast";
import { AdminTeamDrawer } from "@/features/admin/teams/AdminTeamDrawer";
import {
  adminErrorExtension,
  adminErrorMessageKey,
} from "@/features/admin/lib/adminErrorMessage";
import { cn } from "@/lib/utils";

const PAGE_SIZE_OPTIONS = [25, 50, 100] as const;

export function AdminTeamsPage() {
  const { t } = useTranslation("admin");

  const [searchInput, setSearchInput] = useState("");
  const [searchDebounced, setSearchDebounced] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] =
    useState<(typeof PAGE_SIZE_OPTIONS)[number]>(50);
  const [openTeamId, setOpenTeamId] = useState<string | null>(null);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createSlug, setCreateSlug] = useState("");
  const [createDescription, setCreateDescription] = useState("");
  const [toast, setToast] = useState<AdminToastMessage | null>(null);
  const toastSeq = useRef(0);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setSearchDebounced(searchInput);
      setPage(1);
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [searchInput]);

  const queryParams = useMemo(
    () => ({
      page,
      page_size: pageSize,
      search: searchDebounced.trim() || null,
    }),
    [page, pageSize, searchDebounced],
  );

  const teamsQuery = useAdminTeams(queryParams);
  const items = teamsQuery.data?.items ?? [];
  const total = teamsQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const create = useCreateTeam();

  function notify(text: string, tone: "success" | "error", key?: string) {
    toastSeq.current += 1;
    setToast({ id: toastSeq.current, text, tone, key });
  }

  async function handleCreate() {
    if (!createName.trim() || !createSlug.trim()) return;
    try {
      const created = await create.mutateAsync({
        name: createName.trim(),
        slug: createSlug.trim(),
        description: createDescription.trim() || null,
      });
      setShowCreateForm(false);
      setCreateName("");
      setCreateSlug("");
      setCreateDescription("");
      notify(t("admin.teams.toast.created"), "success", "created");
      // Open the freshly-created team's drawer so the admin can immediately
      // add members.
      setOpenTeamId(created.id);
    } catch (err) {
      notify(t(adminErrorMessageKey(err)), "error", adminErrorExtension(err));
    }
  }

  return (
    <div className="flex h-full flex-col" data-testid="admin-teams-page">
      <header className="border-b bg-card px-6 py-4">
        <h1 className="text-lg font-semibold tracking-tight">
          {t("admin.teams.title")}
        </h1>
        <p className="text-sm text-muted-foreground">
          {t("admin.teams.subtitle")}
        </p>
      </header>

      <div
        className="flex flex-wrap items-end gap-3 border-b bg-card px-6 py-3"
        data-testid="admin-teams-toolbar"
      >
        <div className="grow basis-64">
          <Label
            htmlFor="admin-teams-search"
            className="text-xs text-muted-foreground"
          >
            {t("admin.teams.filter.search_placeholder")}
          </Label>
          <Input
            id="admin-teams-search"
            data-testid="admin-teams-search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder={t("admin.teams.filter.search_placeholder")}
            className="h-9"
          />
        </div>
        <Button
          size="sm"
          onClick={() => setShowCreateForm((s) => !s)}
          data-testid="admin-teams-new-button"
        >
          <Plus className="h-4 w-4" aria-hidden />
          {t("admin.teams.action.create")}
        </Button>
      </div>

      {showCreateForm ? (
        <section
          className="space-y-2 border-b bg-muted/20 px-6 py-3"
          data-testid="admin-teams-create-form"
        >
          <div className="grid gap-2 sm:grid-cols-3">
            <div>
              <Label htmlFor="admin-teams-new-name" className="text-xs">
                {t("admin.teams.form.name_label")}
              </Label>
              <Input
                id="admin-teams-new-name"
                data-testid="admin-teams-new-name"
                value={createName}
                onChange={(e) => setCreateName(e.target.value)}
                className="h-9"
              />
            </div>
            <div>
              <Label htmlFor="admin-teams-new-slug" className="text-xs">
                {t("admin.teams.form.slug_label")}
              </Label>
              <Input
                id="admin-teams-new-slug"
                data-testid="admin-teams-new-slug"
                value={createSlug}
                onChange={(e) => setCreateSlug(e.target.value)}
                className="h-9 font-mono text-xs"
              />
            </div>
            <div>
              <Label htmlFor="admin-teams-new-description" className="text-xs">
                {t("admin.teams.form.description_label")}
              </Label>
              <Input
                id="admin-teams-new-description"
                data-testid="admin-teams-new-description"
                value={createDescription}
                onChange={(e) => setCreateDescription(e.target.value)}
                className="h-9"
              />
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setShowCreateForm(false)}
            >
              {t("admin.teams.form.cancel")}
            </Button>
            <Button
              size="sm"
              onClick={handleCreate}
              disabled={
                create.isPending ||
                !createName.trim() ||
                !createSlug.trim()
              }
              data-testid="admin-teams-create-save"
            >
              {create.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              ) : null}
              {t("admin.teams.form.create")}
            </Button>
          </div>
        </section>
      ) : null}

      <div className="flex-1 overflow-y-auto">
        {teamsQuery.isError ? (
          <div className="px-6 py-4">
            <Alert variant="destructive" data-testid="admin-teams-error">
              <AlertDescription>{t("admin.errors.unknown")}</AlertDescription>
            </Alert>
          </div>
        ) : null}

        <table
          className="w-full text-sm"
          data-testid="admin-teams-table"
          aria-busy={teamsQuery.isLoading}
        >
          <thead className="sticky top-0 bg-card">
            <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
              <th className="px-6 py-2">{t("admin.teams.column.name")}</th>
              <th className="px-3 py-2">{t("admin.teams.column.slug")}</th>
              <th className="px-3 py-2">{t("admin.teams.column.description")}</th>
              <th className="px-3 py-2 text-right">
                {t("admin.teams.column.member_count")}
              </th>
              <th className="px-3 py-2 text-right">
                {t("admin.teams.column.project_count")}
              </th>
            </tr>
          </thead>
          <tbody data-testid="admin-teams-tbody">
            {teamsQuery.isLoading
              ? Array.from({ length: 6 }).map((_, i) => (
                  <tr key={`skeleton-${i}`} className="border-b">
                    <td className="px-6 py-2" colSpan={5}>
                      <Skeleton className="h-5 w-full" />
                    </td>
                  </tr>
                ))
              : items.map((team) => (
                  <tr
                    key={team.id}
                    data-testid="admin-teams-row"
                    data-team-id={team.id}
                    data-team-name={team.name}
                    data-team-slug={team.slug}
                    className={cn(
                      "cursor-pointer border-b transition-colors duration-fast ease-out-soft hover:bg-accent/40 focus-within:bg-accent/40",
                    )}
                    style={{ height: "var(--table-row)" }}
                    tabIndex={0}
                    onClick={() => setOpenTeamId(team.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        setOpenTeamId(team.id);
                      }
                    }}
                  >
                    <td className="truncate px-6">{team.name}</td>
                    <td className="truncate px-3 font-mono text-xs">
                      {team.slug}
                    </td>
                    <td className="truncate px-3 text-xs text-muted-foreground">
                      {team.description ?? "—"}
                    </td>
                    <td className="px-3 text-right">{team.member_count}</td>
                    <td className="px-3 text-right">{team.project_count}</td>
                  </tr>
                ))}
            {!teamsQuery.isLoading && items.length === 0 ? (
              <tr>
                <td
                  colSpan={5}
                  className="px-6 py-12 text-center text-sm text-muted-foreground"
                  data-testid="admin-teams-empty"
                >
                  {t("admin.teams.empty")}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      <footer
        className="flex shrink-0 items-center justify-between border-t bg-card px-6 py-2 text-xs"
        data-testid="admin-teams-pagination"
      >
        <div className="flex items-center gap-2">
          <label
            htmlFor="admin-teams-page-size"
            className="text-muted-foreground"
          >
            {t("admin.users.pagination.page_size_label")}
          </label>
          <select
            id="admin-teams-page-size"
            data-testid="admin-teams-page-size"
            className="h-8 rounded-md border border-input bg-background px-2"
            value={pageSize}
            onChange={(e) => {
              setPageSize(
                Number(e.target.value) as (typeof PAGE_SIZE_OPTIONS)[number],
              );
              setPage(1);
            }}
          >
            {PAGE_SIZE_OPTIONS.map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        </div>

        <div className="flex items-center gap-2">
          <span className="text-muted-foreground">
            {t("admin.users.pagination.page_label", {
              page,
              total: totalPages,
            })}
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            data-testid="admin-teams-page-prev"
          >
            {t("admin.users.pagination.previous")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            data-testid="admin-teams-page-next"
          >
            {t("admin.users.pagination.next")}
          </Button>
        </div>
      </footer>

      <AdminTeamDrawer
        open={openTeamId !== null}
        teamId={openTeamId}
        onOpenChange={(open) => {
          if (!open) setOpenTeamId(null);
        }}
        notify={notify}
        onDeleted={() => setOpenTeamId(null)}
      />

      <AdminToast message={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}
