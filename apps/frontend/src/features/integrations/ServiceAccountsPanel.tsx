// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Automation identities, next to the keys they hold.
 *
 * Deliberately here and not on the admin users page. These rows share a table
 * with people, but a user list offers actions that are wrong for them: there
 * is nobody to send a password reset to, and deactivating one from a leavers
 * screen is a pipeline outage that reads as tidying up. Here, beside the keys,
 * deactivating one plainly means "stop these credentials".
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { problemMessage } from "@/lib/problemMessage";
import { useAuthStore } from "@/stores/authStore";
import {
  type ServiceAccountListOut,
  assignServiceAccountSteward,
  createServiceAccount,
  deactivateServiceAccount,
  listServiceAccounts,
} from "@/lib/serviceAccountsApi";

interface ServiceAccountsPanelProps {
  teamId: string | null;
  /** False for a grade that may read the page but not issue credentials. */
  canManage: boolean;
  onNotify: (message: string, tone: "success" | "error", key: string) => void;
}

export function ServiceAccountsPanel({
  teamId,
  canManage,
  onNotify,
}: ServiceAccountsPanelProps) {
  const { t } = useTranslation("integrations");
  const queryClient = useQueryClient();
  const [slug, setSlug] = useState("");
  const [displayName, setDisplayName] = useState("");

  const query = useQuery<ServiceAccountListOut, Error>({
    queryKey: ["service-accounts", teamId ?? "__none__"],
    enabled: teamId !== null,
    queryFn: () => listServiceAccounts(teamId as string),
  });

  const create = useMutation({
    mutationFn: () =>
      createServiceAccount({
        team_id: teamId as string,
        slug: slug.trim(),
        display_name: displayName.trim() || slug.trim(),
      }),
    meta: { errorToast: false },
    onSuccess: () => {
      setSlug("");
      setDisplayName("");
      void queryClient.invalidateQueries({ queryKey: ["service-accounts"] });
      onNotify(t("service_accounts.toast.created"), "success", "sa-created");
    },
    onError: (err) =>
      onNotify(
        problemMessage(err, t, { action: "service_accounts.errors.create_failed" }),
        "error",
        "sa-create-failed",
      ),
  });

  // Taking over an account nobody is answerable for. Without this the panel
  // says "no steward", the server refuses new keys, and the only ways out are
  // re-activating the person who left or leaving live credentials unowned.
  // Both are worse than the state the refusal is trying to prevent.
  const currentUserId = useAuthStore((s) => s.user?.id ?? null);
  const takeOver = useMutation({
    mutationFn: (id: string) =>
      assignServiceAccountSteward(id, currentUserId as string),
    meta: { errorToast: false },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["service-accounts"] });
      onNotify(t("service_accounts.toast.taken_over"), "success", "sa-steward");
    },
    onError: (err) =>
      onNotify(
        problemMessage(err, t, {
          action: "service_accounts.errors.take_over_failed",
        }),
        "error",
        "sa-steward-failed",
      ),
  });

  const deactivate = useMutation({
    mutationFn: (id: string) => deactivateServiceAccount(id),
    meta: { errorToast: false },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["service-accounts"] });
      // Also the keys: deactivating the identity stopped every one of them,
      // and a list still showing them as usable would be a lie.
      void queryClient.invalidateQueries({ queryKey: ["api-keys"] });
      onNotify(
        t("service_accounts.toast.deactivated"),
        "success",
        "sa-deactivated",
      );
    },
    onError: (err) =>
      onNotify(
        problemMessage(err, t, {
          action: "service_accounts.errors.deactivate_failed",
        }),
        "error",
        "sa-deactivate-failed",
      ),
  });

  if (teamId === null) return null;
  const items = query.data?.items ?? [];

  return (
    <section className="space-y-3" data-testid="service-accounts-panel">
      <div>
        <h2 className="text-sm font-semibold tracking-tight">
          {t("service_accounts.title")}
        </h2>
        <p className="text-xs text-muted-foreground">
          {t("service_accounts.description")}
        </p>
      </div>

      {items.length > 0 ? (
        <ul className="divide-y rounded-lg border">
          {items.map((account) => (
            <li
              key={account.id}
              className="flex flex-wrap items-center justify-between gap-3 p-3"
              data-testid={`service-account-${account.id}`}
            >
              <div className="min-w-0 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium">
                    {account.full_name ?? account.email}
                  </span>
                  {!account.is_active ? (
                    <Badge
                      variant="outline"
                      data-testid="service-account-stopped"
                    >
                      {t("service_accounts.stopped")}
                    </Badge>
                  ) : account.managed_by_user_id === null ? (
                    // The state that matters: existing keys still work, and no
                    // new one may be issued until somebody takes it over.
                    <Badge
                      variant="outline"
                      className="border-status-warning-border bg-status-warning-subtle text-status-warning-foreground"
                      data-testid="service-account-unowned"
                    >
                      {t("service_accounts.unowned")}
                    </Badge>
                  ) : null}
                </div>
                <p className="font-mono text-xs text-muted-foreground">
                  {account.email}
                </p>
              </div>
              <div className="flex items-center gap-2">
              {canManage &&
              account.is_active &&
              account.managed_by_user_id === null &&
              currentUserId !== null ? (
                <Button
                  type="button"
                  size="sm"
                  disabled={takeOver.isPending}
                  onClick={() => takeOver.mutate(account.id)}
                  data-testid={`service-account-take-over-${account.id}`}
                  title={t("service_accounts.take_over_help")}
                >
                  {t("service_accounts.take_over")}
                </Button>
              ) : null}
              {canManage && account.is_active ? (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={deactivate.isPending}
                  onClick={() => deactivate.mutate(account.id)}
                  data-testid={`service-account-deactivate-${account.id}`}
                  title={t("service_accounts.deactivate_help")}
                >
                  {t("service_accounts.deactivate")}
                </Button>
              ) : null}
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p
          className="text-xs text-muted-foreground"
          data-testid="service-accounts-empty"
        >
          {t("service_accounts.empty")}
        </p>
      )}

      {canManage ? (
        <form
          className="flex flex-wrap items-end gap-2"
          data-testid="service-accounts-create-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (slug.trim()) create.mutate();
          }}
        >
          <div className="space-y-1">
            <Label htmlFor="service-account-slug" className="text-xs">
              {t("service_accounts.slug_label")}
            </Label>
            <Input
              id="service-account-slug"
              value={slug}
              onChange={(event) => setSlug(event.target.value)}
              placeholder="nightly-build"
              data-testid="service-account-slug"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="service-account-name" className="text-xs">
              {t("service_accounts.name_label")}
            </Label>
            <Input
              id="service-account-name"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              data-testid="service-account-name"
            />
          </div>
          <Button
            type="submit"
            size="sm"
            disabled={!slug.trim() || create.isPending}
            data-testid="service-account-create"
          >
            {t("service_accounts.create")}
          </Button>
        </form>
      ) : null}
    </section>
  );
}
