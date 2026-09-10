// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
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
import { problemMessage } from "@/lib/problemMessage";
import { useActiveTeam } from "@/hooks/useActiveTeam";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { useAuthStore } from "@/stores/authStore";
import {
  TeamCombobox,
  type TeamComboboxSelection,
} from "@/features/projects/components/TeamCombobox";

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
  useDocumentTitle(t("create.title"));
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);

  // Team resolution (fix for the create-project 422): /auth/me now returns the
  // caller's memberships, so we have a real team_id. Single-team users get it
  // implicitly; multi-team users pick from a selector. Without any membership
  // we block submit instead of POSTing an empty team_id (which 422s).
  const teams = user?.teams ?? [];
  // W14 — follow the global bar's team switcher. Reading `user.teamId` into
  // a useState initialiser meant the form kept whatever team was active when
  // it mounted: switch teams in the bar while this page is open and the bar
  // said one thing while submit POSTed another. `useActiveTeam` is the single
  // resolution, and the effect below re-syncs when the user changes it. An
  // explicit pick in the combobox still wins until the bar moves again.
  //
  // No separate `user?.teamId` fallback here (there used to be one): it
  // duplicated `useActiveTeam`'s own no-stored-preference fallback, and
  // duplicating it meant that when `useActiveTeam` returns `null` because
  // the stored choice names a group the user has no direct membership in
  // (group-hierarchy cascade case, see that hook's own docstring), this
  // component quietly fell back to `user.teamId` anyway, reintroducing,
  // one level down, the exact silent-team-substitution bug `useActiveTeam`
  // exists to prevent.
  //
  // group-hierarchy Phase 6: `teamId` alone is no longer enough to drive the
  // picker: a cascade-reached group picked via the combobox's search has no
  // entry in `teams`, so there is nowhere to look its name back up once the
  // popover closes. `selectedTeam` carries the id AND the display name
  // together, still resolving all the way down to `null` (not a silent
  // substitute) exactly when `activeTeam` does.
  const activeTeam = useActiveTeam();
  const [selectedTeam, setSelectedTeam] = useState<TeamComboboxSelection | null>(
    activeTeam ? { id: activeTeam.id, name: activeTeam.name } : null,
  );
  useEffect(() => {
    setSelectedTeam(activeTeam ? { id: activeTeam.id, name: activeTeam.name } : null);
  }, [activeTeam]);
  const teamId = selectedTeam?.id ?? "";
  const hasTeam = teamId !== "";
  // Distinguishes the two reasons `hasTeam` can be false: no membership at
  // all (existing `create.no_team` copy is accurate) vs. a real membership
  // that just isn't the currently active selection (the cascade case above).
  // Reusing "you are not a member of any team" there would be wrong copy;
  // the user IS a member of teams, just not the one currently active.
  const activeTeamUnresolved = !hasTeam && teams.length > 0;

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
    // Error surfaced locally (toast/inline) — keep the global error toast quiet.
    meta: { errorToast: false },
    onSuccess: (project) => {
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      navigate(`/projects/${project.id}`);
    },
  });

  function onSubmit(values: FormValues) {
    mutation.mutate(values);
  }

  const submitError = mutation.isError
    ? problemMessage(mutation.error, t, {
        // This surface knows what its 409 means (a name already taken), so it
        // says that instead of the generic "does not match current state".
        prefix: "create.errors",
        action: "create.error_failed",
      })
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

        {/*
          group-hierarchy Phase 6: the combobox is always shown, unlike the
          old `<select>` (gated on `teams.length > 1 || activeTeamUnresolved`).
          Search reaches groups outside `teams` (the cascade case this task
          exists for), so even a single-team user benefits, and the blocked
          `!hasTeam` alert below still needs SOME way out for the
          active-team-unresolved case (group-hierarchy Phase 5 security
          review) -- the trigger shows the placeholder, never a silently
          pre-selected name, whenever `selectedTeam` is `null`.
        */}
        <div className="space-y-1.5">
          <Label htmlFor="project-team">{t("create.team_label")}</Label>
          <TeamCombobox
            triggerId="project-team"
            teams={teams}
            selected={selectedTeam}
            onSelect={setSelectedTeam}
            placeholder={t("create.team_select_placeholder")}
          />
        </div>

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
          <Alert
            variant="destructive"
            data-testid="project-create-no-team"
            data-reason={activeTeamUnresolved ? "active_team_unresolved" : "no_team"}
          >
            <AlertDescription>
              {activeTeamUnresolved
                ? t("create.active_team_unresolved")
                : t("create.no_team")}
            </AlertDescription>
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
