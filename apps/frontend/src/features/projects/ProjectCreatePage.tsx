import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { z } from "zod";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { PageTitle, Subtitle } from "@/components/ui/typography";
import { createProject } from "@/lib/projectsApi";
import { ProblemError } from "@/lib/problem";
import { useAuthStore } from "@/stores/authStore";

type FormValues = {
  name: string;
  description: string;
  git_url: string;
  default_branch: string;
};

function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/\s+/g, "-")
    .replace(/[^a-z0-9-]/g, "");
}

export function ProjectCreatePage() {
  const { t } = useTranslation("projects");
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);

  // Team resolution (fix for the create-project 422): /auth/me now returns the
  // caller's memberships, so we have a real team_id. Single-team users get it
  // implicitly; multi-team users pick from a selector. Without any membership
  // we block submit instead of POSTing an empty team_id (which 422s).
  const teams = user?.teams ?? [];
  const [teamId, setTeamId] = useState<string>(user?.teamId ?? "");
  const hasTeam = teamId !== "";

  const formSchema = z.object({
    name: z
      .string()
      .min(1, t("create.error_name_required"))
      .max(100, t("create.error_name_max")),
    description: z
      .string()
      .max(500, t("create.error_description_max")),
    git_url: z
      .string()
      .refine(
        // Mirror the backend _GIT_URL_PATTERN (apps/backend/schemas/scan.py):
        // https/ssh/git+ssh/git URLs and the git@host: SCP form. The previous
        // ^https? mirror wrongly rejected ssh:// and git@ URLs the backend
        // accepts. Backend stays the source of truth.
        (v) =>
          v === "" ||
          /^(https?:\/\/|ssh:\/\/|git\+ssh:\/\/|git:\/\/|[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:).+/i.test(
            v,
          ),
        t("create.error_git_url_invalid"),
      ),
    default_branch: z
      .string()
      .refine(
        (v) => v === "" || /^[A-Za-z0-9._/-]{1,255}$/.test(v),
        t("create.error_default_branch_invalid"),
      ),
  });

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: { name: "", description: "", git_url: "", default_branch: "" },
  });

  const mutation = useMutation({
    mutationFn: (values: FormValues) =>
      createProject({
        team_id: teamId,
        name: values.name,
        slug: slugify(values.name),
        description: values.description || null,
        git_url: values.git_url || null,
        default_branch: values.default_branch || null,
      }),
    onSuccess: (project) => {
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      navigate(`/projects/${project.id}`);
    },
  });

  function onSubmit(values: FormValues) {
    mutation.mutate(values);
  }

  const submitError = mutation.isError
    ? mutation.error instanceof ProblemError
      ? mutation.error.detail
      : mutation.error.message
    : null;

  return (
    <div className="mx-auto max-w-lg px-6 py-10">
      <PageTitle className="mb-1">{t("create.title")}</PageTitle>
      <Subtitle className="mb-6" data-testid="project-create-scan-hint">
        {t("create.scan_hint")}
      </Subtitle>

      <form
        onSubmit={handleSubmit(onSubmit)}
        data-testid="project-create-form"
        noValidate
        className="space-y-5"
      >
        <div className="space-y-1.5">
          <Label htmlFor="project-name">
            {t("create.name_label")}
            <span className="ml-0.5 text-destructive" aria-hidden>
              *
            </span>
          </Label>
          <Input
            id="project-name"
            placeholder={t("create.name_placeholder")}
            {...register("name")}
            data-testid="project-name-input"
            aria-invalid={errors.name ? "true" : "false"}
            aria-describedby={errors.name ? "project-name-error" : undefined}
          />
          {errors.name ? (
            <p
              id="project-name-error"
              data-testid="project-name-error"
              className="text-xs text-destructive"
              aria-live="polite"
            >
              {errors.name.message}
            </p>
          ) : null}
        </div>

        {teams.length > 1 ? (
          <div className="space-y-1.5">
            <Label htmlFor="project-team">{t("create.team_label")}</Label>
            <select
              id="project-team"
              value={teamId}
              onChange={(e) => setTeamId(e.target.value)}
              data-testid="project-team-select"
              className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors duration-fast ease-out-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              {teams.map((tm) => (
                <option key={tm.id} value={tm.id}>
                  {tm.name}
                </option>
              ))}
            </select>
          </div>
        ) : null}

        <div className="space-y-1.5">
          <Label htmlFor="project-description">
            {t("create.description_label")}
          </Label>
          <Textarea
            id="project-description"
            placeholder={t("create.description_placeholder")}
            rows={3}
            {...register("description")}
            data-testid="project-description-input"
            aria-invalid={errors.description ? "true" : "false"}
            aria-describedby={
              errors.description ? "project-description-error" : undefined
            }
          />
          {errors.description ? (
            <p
              id="project-description-error"
              className="text-xs text-destructive"
              aria-live="polite"
            >
              {errors.description.message}
            </p>
          ) : null}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="project-git-url">
            {t("create.git_url_label")}
          </Label>
          <Input
            id="project-git-url"
            type="url"
            placeholder={t("create.git_url_placeholder")}
            {...register("git_url")}
            data-testid="project-git-url-input"
            aria-invalid={errors.git_url ? "true" : "false"}
            aria-describedby={
              errors.git_url ? "project-git-url-error" : undefined
            }
          />
          {errors.git_url ? (
            <p
              id="project-git-url-error"
              className="text-xs text-destructive"
              aria-live="polite"
            >
              {errors.git_url.message}
            </p>
          ) : (
            <p className="text-xs text-muted-foreground">
              {t("create.git_url_hint")}
            </p>
          )}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="project-default-branch">
            {t("create.default_branch_label")}
          </Label>
          <Input
            id="project-default-branch"
            placeholder={t("create.default_branch_placeholder")}
            {...register("default_branch")}
            data-testid="project-default-branch-input"
            aria-invalid={errors.default_branch ? "true" : "false"}
            aria-describedby={
              errors.default_branch ? "project-default-branch-error" : undefined
            }
          />
          {errors.default_branch ? (
            <p
              id="project-default-branch-error"
              className="text-xs text-destructive"
              aria-live="polite"
            >
              {errors.default_branch.message}
            </p>
          ) : null}
        </div>

        {!hasTeam ? (
          <Alert variant="destructive" data-testid="project-create-no-team">
            <AlertDescription>{t("create.no_team")}</AlertDescription>
          </Alert>
        ) : null}

        {submitError ? (
          <Alert variant="destructive" data-testid="project-create-error">
            <AlertDescription>{submitError}</AlertDescription>
          </Alert>
        ) : null}

        <div className="flex gap-3">
          <Button
            type="submit"
            disabled={mutation.isPending || !hasTeam}
            data-testid="project-create-submit"
          >
            {t("create.submit")}
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => navigate("/projects")}
            disabled={mutation.isPending}
          >
            {t("create.cancel")}
          </Button>
        </div>
      </form>
    </div>
  );
}
