/**
 * CreateApiKeyDialog — chore C.
 *
 * shadcn `Dialog` form for issuing a new API key. The scope select is a
 * native `<select>` (no shadcn Select primitive in the tree yet); the
 * conditional team_id / project_id text fields appear only when the
 * relevant scope is selected.
 *
 * The plaintext bearer string is NOT shown here — the page lifts it into
 * `RevealApiKeyDialog` on success because the create response is the only
 * place the backend ever surfaces the raw key.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { APIKeyCreatePayload, APIKeyScope } from "@/types/apiKey";

interface CreateApiKeyDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (payload: APIKeyCreatePayload) => void;
  submitting: boolean;
}

export function CreateApiKeyDialog({
  open,
  onOpenChange,
  onSubmit,
  submitting,
}: CreateApiKeyDialogProps) {
  const { t } = useTranslation("integrations");
  const [name, setName] = useState("");
  const [scope, setScope] = useState<APIKeyScope>("project");
  const [teamId, setTeamId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setName("");
    setScope("project");
    setTeamId("");
    setProjectId("");
    setError(null);
  }

  function handleOpenChange(next: boolean) {
    if (!next) reset();
    onOpenChange(next);
  }

  function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);

    if (!name.trim()) {
      setError(t("api_keys.errors.name_required"));
      return;
    }
    if (scope === "team" && !teamId.trim()) {
      setError(t("api_keys.errors.team_id_required"));
      return;
    }
    if (scope === "project" && !projectId.trim()) {
      setError(t("api_keys.errors.project_id_required"));
      return;
    }

    onSubmit({
      name: name.trim(),
      scope,
      team_id: scope === "team" ? teamId.trim() : null,
      project_id: scope === "project" ? projectId.trim() : null,
    });
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent data-testid="integrations-create-dialog">
        <DialogHeader>
          <DialogTitle>{t("api_keys.create_dialog.title")}</DialogTitle>
          <DialogDescription>
            {t("api_keys.create_dialog.description")}
          </DialogDescription>
        </DialogHeader>

        <form
          onSubmit={handleSubmit}
          className="space-y-4"
          data-testid="integrations-create-form"
          noValidate
        >
          <div className="space-y-1.5">
            <Label htmlFor="apikey-name">
              {t("api_keys.create_dialog.name_label")}
            </Label>
            <Input
              id="apikey-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("api_keys.create_dialog.name_placeholder")}
              data-testid="integrations-create-name"
              disabled={submitting}
              autoFocus
              required
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="apikey-scope">
              {t("api_keys.create_dialog.scope_label")}
            </Label>
            <select
              id="apikey-scope"
              value={scope}
              onChange={(e) => setScope(e.target.value as APIKeyScope)}
              data-testid="integrations-create-scope"
              disabled={submitting}
              className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors duration-fast ease-out-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <option value="project">{t("api_keys.scope.project")}</option>
              <option value="team">{t("api_keys.scope.team")}</option>
              <option value="org">{t("api_keys.scope.org")}</option>
            </select>
            <p className="text-xs text-muted-foreground">
              {scope === "org"
                ? t("api_keys.create_dialog.scope_help_org")
                : scope === "team"
                  ? t("api_keys.create_dialog.scope_help_team")
                  : t("api_keys.create_dialog.scope_help_project")}
            </p>
          </div>

          {scope === "team" ? (
            <div className="space-y-1.5">
              <Label htmlFor="apikey-team-id">
                {t("api_keys.create_dialog.team_id_label")}
              </Label>
              <Input
                id="apikey-team-id"
                value={teamId}
                onChange={(e) => setTeamId(e.target.value)}
                placeholder={t("api_keys.create_dialog.team_id_placeholder")}
                data-testid="integrations-create-team-id"
                disabled={submitting}
              />
            </div>
          ) : null}

          {scope === "project" ? (
            <div className="space-y-1.5">
              <Label htmlFor="apikey-project-id">
                {t("api_keys.create_dialog.project_id_label")}
              </Label>
              <Input
                id="apikey-project-id"
                value={projectId}
                onChange={(e) => setProjectId(e.target.value)}
                placeholder={t(
                  "api_keys.create_dialog.project_id_placeholder",
                )}
                data-testid="integrations-create-project-id"
                disabled={submitting}
              />
            </div>
          ) : null}

          {error ? (
            <p
              className="text-xs text-destructive"
              role="alert"
              aria-live="polite"
              data-testid="integrations-create-error"
            >
              {error}
            </p>
          ) : null}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => handleOpenChange(false)}
              disabled={submitting}
              data-testid="integrations-create-cancel"
            >
              {t("api_keys.create_dialog.cancel")}
            </Button>
            <Button
              type="submit"
              disabled={submitting}
              data-testid="integrations-create-submit"
            >
              {submitting
                ? t("api_keys.create_dialog.submitting")
                : t("api_keys.create_dialog.submit")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
