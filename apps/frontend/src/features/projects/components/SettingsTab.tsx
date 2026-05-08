/**
 * SettingsTab — Phase 3 / Step 4-B.
 *
 * Editable subset of the project's metadata: name, description, git URL,
 * default branch. Powered by react-hook-form + zod (mirrors the
 * `ProjectCreatePage` pattern). Save dispatches `PATCH /v1/projects/{id}`.
 *
 * Archive / Unarchive are sibling actions:
 *   - Archive uses an inline confirm strip before dispatching the API call.
 *     The portal models archive as a soft-delete (`DELETE /v1/projects/{id}`),
 *     which is what `archiveProject` wraps.
 *   - Unarchive dispatches `PATCH { archived: false }` directly — there is
 *     no destructive intent so the confirm strip is unnecessary.
 *
 * Errors surface inline (`SettingsTab` Alert) so the operator sees the
 * RFC 7807 `detail` next to the form, not in a toast they might miss.
 */
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ProblemError } from "@/lib/problem";
import {
  archiveProject,
  type ProjectPublic,
  unarchiveProject,
  updateProject,
} from "@/lib/projectsApi";

interface SettingsTabProps {
  projectId: string;
  project: ProjectPublic | null;
}

interface FormValues {
  name: string;
  description: string;
  git_url: string;
  default_branch: string;
}

export function SettingsTab({ projectId, project }: SettingsTabProps) {
  const { t } = useTranslation("project_detail");
  const queryClient = useQueryClient();
  const [confirmingArchive, setConfirmingArchive] = useState(false);
  const [actionToast, setActionToast] = useState<string | null>(null);

  const formSchema = z.object({
    name: z
      .string()
      .min(1, t("settings.errors.name_required"))
      .max(100, t("settings.errors.name_max")),
    description: z.string().max(500, t("settings.errors.description_max")),
    git_url: z
      .string()
      .refine(
        (v) => v === "" || /^https?:\/\//i.test(v),
        t("settings.errors.git_url_invalid"),
      ),
    default_branch: z
      .string()
      .max(255, t("settings.errors.default_branch_max")),
  });

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isDirty },
  } = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      name: project?.name ?? "",
      description: project?.description ?? "",
      git_url: project?.git_url ?? "",
      default_branch: project?.default_branch ?? "",
    },
  });

  // When the underlying project changes (cache hydration / refetch after a
  // sibling tab triggers a mutation), re-prime the form so we don't render
  // stale defaults. We only reset when the form is pristine — otherwise the
  // user's in-flight edits would silently disappear.
  useEffect(() => {
    if (!project || isDirty) return;
    reset({
      name: project.name ?? "",
      description: project.description ?? "",
      git_url: project.git_url ?? "",
      default_branch: project.default_branch ?? "",
    });
  }, [project, reset, isDirty]);

  const saveMutation = useMutation({
    mutationFn: (values: FormValues) =>
      updateProject(projectId, {
        name: values.name,
        description: values.description || null,
        git_url: values.git_url || null,
        default_branch: values.default_branch || null,
      }),
    onSuccess: (next) => {
      void queryClient.invalidateQueries({
        queryKey: ["projects", projectId, "summary"],
      });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      reset({
        name: next.name ?? "",
        description: next.description ?? "",
        git_url: next.git_url ?? "",
        default_branch: next.default_branch ?? "",
      });
      setActionToast(t("settings.toast.saved"));
    },
  });

  const archiveMutation = useMutation({
    mutationFn: () => archiveProject(projectId),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["projects", projectId, "summary"],
      });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      setConfirmingArchive(false);
      setActionToast(t("settings.toast.archived"));
    },
  });

  const unarchiveMutation = useMutation({
    mutationFn: () => unarchiveProject(projectId),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["projects", projectId, "summary"],
      });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      setActionToast(t("settings.toast.unarchived"));
    },
  });

  const isArchived = project?.archived_at != null;

  const submitError = (() => {
    const err = saveMutation.error;
    if (!err) return null;
    if (err instanceof ProblemError) return err.detail;
    return err instanceof Error ? err.message : String(err);
  })();

  const archiveError = (() => {
    const err = archiveMutation.error ?? unarchiveMutation.error;
    if (!err) return null;
    if (err instanceof ProblemError) return err.detail;
    return err instanceof Error ? err.message : String(err);
  })();

  function onSubmit(values: FormValues) {
    setActionToast(null);
    saveMutation.mutate(values);
  }

  return (
    <div
      className="mx-auto max-w-2xl p-6"
      data-testid="settings-tab"
      data-archived={isArchived ? "true" : "false"}
    >
      <h2 className="mb-1 text-lg font-semibold">{t("settings.title")}</h2>
      <p className="mb-6 text-xs text-muted-foreground">
        {t("settings.subtitle")}
      </p>

      <form
        onSubmit={handleSubmit(onSubmit)}
        data-testid="settings-form"
        noValidate
        className="space-y-5"
      >
        <div className="space-y-1.5">
          <Label htmlFor="settings-name">
            {t("settings.field.name")}
            <span className="ml-0.5 text-destructive" aria-hidden>
              *
            </span>
          </Label>
          <Input
            id="settings-name"
            {...register("name")}
            data-testid="settings-name-input"
            aria-invalid={errors.name ? "true" : "false"}
            aria-describedby={errors.name ? "settings-name-error" : undefined}
          />
          {errors.name ? (
            <p
              id="settings-name-error"
              data-testid="settings-name-error"
              className="text-xs text-destructive"
              aria-live="polite"
            >
              {errors.name.message}
            </p>
          ) : null}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="settings-description">
            {t("settings.field.description")}
          </Label>
          <Textarea
            id="settings-description"
            rows={3}
            {...register("description")}
            data-testid="settings-description-input"
            aria-invalid={errors.description ? "true" : "false"}
            aria-describedby={
              errors.description ? "settings-description-error" : undefined
            }
          />
          {errors.description ? (
            <p
              id="settings-description-error"
              data-testid="settings-description-error"
              className="text-xs text-destructive"
              aria-live="polite"
            >
              {errors.description.message}
            </p>
          ) : null}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="settings-git-url">
            {t("settings.field.git_url")}
          </Label>
          <Input
            id="settings-git-url"
            type="url"
            placeholder={t("settings.field.git_url_placeholder")}
            {...register("git_url")}
            data-testid="settings-git-url-input"
            aria-invalid={errors.git_url ? "true" : "false"}
            aria-describedby={
              errors.git_url ? "settings-git-url-error" : undefined
            }
          />
          {errors.git_url ? (
            <p
              id="settings-git-url-error"
              data-testid="settings-git-url-error"
              className="text-xs text-destructive"
              aria-live="polite"
            >
              {errors.git_url.message}
            </p>
          ) : null}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="settings-default-branch">
            {t("settings.field.default_branch")}
          </Label>
          <Input
            id="settings-default-branch"
            placeholder={t("settings.field.default_branch_placeholder")}
            {...register("default_branch")}
            data-testid="settings-default-branch-input"
            aria-invalid={errors.default_branch ? "true" : "false"}
          />
          {errors.default_branch ? (
            <p
              data-testid="settings-default-branch-error"
              className="text-xs text-destructive"
              aria-live="polite"
            >
              {errors.default_branch.message}
            </p>
          ) : null}
        </div>

        {submitError ? (
          <Alert variant="destructive" data-testid="settings-save-error">
            <AlertDescription>{submitError}</AlertDescription>
          </Alert>
        ) : null}

        <div className="flex items-center gap-3">
          <Button
            type="submit"
            disabled={saveMutation.isPending}
            data-testid="settings-save-button"
          >
            {saveMutation.isPending
              ? t("settings.action.saving")
              : t("settings.action.save")}
          </Button>
          {actionToast ? (
            <span
              className="text-xs text-emerald-700"
              data-testid="settings-toast"
              aria-live="polite"
            >
              {actionToast}
            </span>
          ) : null}
        </div>
      </form>

      <hr className="my-8 border-border" />

      <section
        className="space-y-3"
        data-testid="settings-archive-section"
      >
        <div>
          <h3 className="text-sm font-semibold">
            {isArchived
              ? t("settings.archive.archived_title")
              : t("settings.archive.active_title")}
          </h3>
          <p className="text-xs text-muted-foreground">
            {isArchived
              ? t("settings.archive.archived_subtitle")
              : t("settings.archive.active_subtitle")}
          </p>
        </div>

        {archiveError ? (
          <Alert variant="destructive" data-testid="settings-archive-error">
            <AlertDescription>{archiveError}</AlertDescription>
          </Alert>
        ) : null}

        {isArchived ? (
          <Button
            type="button"
            variant="outline"
            disabled={unarchiveMutation.isPending}
            onClick={() => {
              setActionToast(null);
              unarchiveMutation.mutate();
            }}
            data-testid="settings-unarchive-button"
          >
            {unarchiveMutation.isPending
              ? t("settings.action.unarchiving")
              : t("settings.action.unarchive")}
          </Button>
        ) : !confirmingArchive ? (
          <Button
            type="button"
            variant="destructive"
            onClick={() => {
              setActionToast(null);
              setConfirmingArchive(true);
            }}
            data-testid="settings-archive-button"
          >
            {t("settings.action.archive")}
          </Button>
        ) : (
          <div
            className="flex flex-col gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
            data-testid="settings-archive-confirm"
          >
            <p>{t("settings.archive.confirm")}</p>
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => setConfirmingArchive(false)}
                data-testid="settings-archive-cancel"
              >
                {t("settings.action.cancel")}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="destructive"
                disabled={archiveMutation.isPending}
                onClick={() => archiveMutation.mutate()}
                data-testid="settings-archive-confirm-ok"
              >
                {archiveMutation.isPending
                  ? t("settings.action.archiving")
                  : t("settings.action.confirm_archive")}
              </Button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
