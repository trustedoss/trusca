// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Getting started checklist (C2).
 *
 * A new organisation logs in to a dashboard with nothing on it and no idea
 * what the product wants from them next. The empty state it replaced said one
 * thing, "register a project", and then went quiet: nothing told them a
 * project without a scan shows nothing, that licence rules are theirs to set,
 * or that CI needs a key. Four steps, in the order the product actually needs
 * them, each one a link to the screen that does it.
 *
 * Every step's done-state is read from the API rather than remembered
 * locally, so it stays true for a colleague who did the work on another
 * machine, and cannot drift from what the server thinks. Each condition below
 * was checked against a freshly migrated, genuinely empty database rather
 * than inferred from the code that serves it.
 *
 * The whole card disappears when the four are done. It is a scaffold, not a
 * feature.
 */
import {
  Boxes,
  Check,
  FolderPlus,
  KeyRound,
  Scale,
  ScanLine,
  X,
} from "lucide-react";
import type { ComponentType, SVGProps } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useApiKeys } from "@/features/integrations/useApiKeys";
import { useLicensePolicies } from "@/features/policies/useLicensePolicies";
import { useActiveTeam } from "@/hooks/useActiveTeam";
import { useDemoMode } from "@/hooks/useDemoMode";
import { cn } from "@/lib/utils";
import { useUIStore } from "@/stores/uiStore";
import type { ProjectPublic } from "@/lib/projectsApi";

/** Only the `total` is wanted from either list; the rows are thrown away. */
const COUNT_ONLY = { page: 1, page_size: 1 } as const;

export type ChecklistStepKey = "project" | "scan" | "policy" | "apiKey";

interface ChecklistStep {
  key: ChecklistStepKey;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  to: string;
  done: boolean;
}

export interface OnboardingChecklistProps {
  /**
   * The dashboard's own projects page. Steps one and two are read from it, so
   * the checklist costs two requests rather than four.
   */
  projects: ProjectPublic[];
  /** True once the projects query has resolved without error. */
  projectsLoaded: boolean;
}

/**
 * Whether the checklist has anything to say, and what it would say.
 *
 * Exported because the dashboard needs the answer before it decides whether
 * to render its own "no projects yet" empty state underneath: two cards both
 * telling the reader to register their first project is worse than either
 * alone. Both callers run this same function, so the two cannot disagree
 * about whether the card is there, and the queries are shared by key rather
 * than issued twice.
 */
export function useOnboardingChecklist({
  projects,
  projectsLoaded,
}: OnboardingChecklistProps): {
  visible: boolean;
  steps: ChecklistStep[];
  doneCount: number;
} {
  const dismissed = useUIStore((s) => s.onboardingDismissed);
  // Dismissed means gone for good, so stop asking. Without this the two
  // reads went out on every dashboard load, for ever, on behalf of a card
  // that will never be drawn again.
  const policiesQuery = useLicensePolicies(COUNT_ONLY, { enabled: !dismissed });
  const apiKeysQuery = useApiKeys(COUNT_ONLY, { enabled: !dismissed });

  const steps: ChecklistStep[] = [
    {
      key: "project",
      icon: FolderPlus,
      to: "/projects/new",
      done: projects.length > 0,
    },
    {
      // `release_count` counts scans that succeeded AND are still live: scan
      // retention marks an older snapshot superseded, and the server leaves
      // those out. Both halves are wanted here. A queued or failed scan means
      // the reader pressed the button, not that they have a component list;
      // a superseded one has been replaced by a newer success, which would
      // itself be counted.
      //
      // Read across one page of 100 projects, which is what the dashboard
      // fetches. An organisation with more than that, whose first hundred by
      // name have never been scanned, would be told to run its first scan.
      // The card is dismissible, and this is the page size to widen if that
      // ever stops being an acceptable trade.
      key: "scan",
      icon: ScanLine,
      to: "/projects",
      done: projects.some((project) => project.release_count > 0),
    },
    {
      // A policy row exists per team, or once per organisation as a default,
      // and an org default is visible to every member of every team in it.
      // Nothing anywhere records that a person read one. So this step can
      // only mean "a policy applies to you" - not "you wrote it", and not
      // "you reviewed it" - and the done copy says exactly that much. What
      // happens meanwhile is in the hint: the built-in categories apply, so
      // an unticked box is a decision not yet made rather than a hole.
      key: "policy",
      icon: Scale,
      to: "/policies",
      done: (policiesQuery.data?.total ?? 0) > 0,
    },
    {
      // The endpoint's default excludes revoked keys but NOT expired ones:
      // `list_api_keys` filters on `revoked_at` alone, while the auth path
      // checks expiry too. So this counts keys that were issued and not
      // taken back, which is what the done copy claims and no more.
      key: "apiKey",
      icon: KeyRound,
      to: "/integrations",
      done: (apiKeysQuery.data?.total ?? 0) > 0,
    },
  ];

  const doneCount = steps.filter((step) => step.done).length;

  // Every step has to be known before any of them is drawn. An unchecked box
  // is a claim that the step is outstanding, and a query that has not
  // answered yet supports no such claim. It also means a policy-endpoint
  // outage hides the checklist rather than telling a reader who has a policy
  // that they have none.
  const known =
    projectsLoaded &&
    policiesQuery.data !== undefined &&
    apiKeysQuery.data !== undefined;

  return {
    // Gone once the work is done: a checklist that stays after its last tick
    // is furniture. Nothing is written on completion, so an organisation that
    // later revokes its only API key gets the reminder back, which is the
    // honest behaviour for a card that reads live state.
    visible: !dismissed && known && doneCount < steps.length,
    steps,
    doneCount,
  };
}

export function OnboardingChecklist(props: OnboardingChecklistProps) {
  const { t } = useTranslation("dashboard");
  const { demoReadOnly } = useDemoMode();
  const dismiss = useUIStore((s) => s.dismissOnboarding);
  const activeTeam = useActiveTeam();
  const { visible, steps, doneCount } = useOnboardingChecklist(props);

  if (!visible) return null;

  // Every one of these four needs a team. A project belongs to one, a policy
  // is written per team, and a key can only be scoped to a team or to a
  // project inside one - so a user with no membership cannot finish a single
  // step, and each CTA would be a door that does not open. They still see
  // what the four steps are; what they get instead of buttons is the reason.
  const hasTeam = activeTeam !== null;

  return (
    <Card data-testid="onboarding-checklist" className="mx-6 mt-6">
      <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
        <div>
          <CardTitle className="flex items-center gap-2 text-base">
            <Boxes className="h-4 w-4 text-brand" aria-hidden />
            {t("onboarding.title")}
          </CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">
            {hasTeam ? t("onboarding.description") : t("onboarding.no_team")}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <span
            className="text-xs tabular-nums text-muted-foreground"
            data-testid="onboarding-progress"
          >
            {t("onboarding.progress", {
              done: doneCount,
              total: steps.length,
            })}
          </span>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={dismiss}
            data-testid="onboarding-dismiss"
            aria-label={t("onboarding.dismiss")}
          >
            <X className="h-4 w-4" aria-hidden />
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <ol className="space-y-1" data-testid="onboarding-steps">
          {steps.map((step) => (
            <ChecklistRow
              key={step.key}
              step={step}
              blocked={!hasTeam}
              demoReadOnly={demoReadOnly}
            />
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}

function ChecklistRow({
  step,
  blocked,
  demoReadOnly,
}: {
  step: ChecklistStep;
  /** The action is unavailable to this user; explain instead of linking. */
  blocked: boolean;
  demoReadOnly: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const Icon = step.icon;
  const label = t(`onboarding.step.${step.key}.label`);
  // The hint stays whatever the step is about. The reason an action is
  // unavailable is said once, in the card header, rather than four times.
  const hint = step.done
    ? t(`onboarding.step.${step.key}.done`)
    : t(`onboarding.step.${step.key}.hint`);
  // A demo deployment refuses writes, so its CTAs would 403. The step still
  // shows its state; it just does not invite an action that cannot happen.
  const inert = blocked || demoReadOnly;

  return (
    <li
      data-testid={`onboarding-step-${step.key}`}
      data-done={step.done}
      className="flex items-center gap-3 rounded-md px-2 py-2"
    >
      <span
        className={cn(
          "flex h-6 w-6 shrink-0 items-center justify-center rounded-full border",
          step.done
            ? "border-brand bg-brand-subtle text-brand"
            : "border-border text-muted-foreground",
        )}
        aria-hidden
      >
        {step.done ? (
          <Check className="h-3.5 w-3.5" />
        ) : (
          <Icon className="h-3.5 w-3.5" />
        )}
      </span>
      <div className="min-w-0 flex-1">
        <div
          className={cn(
            "text-sm font-medium",
            // Struck through as well as ticked, so the state does not rest on
            // the brand colour alone.
            step.done && "text-muted-foreground line-through",
          )}
        >
          {label}
        </div>
        <p className="text-xs text-muted-foreground">{hint}</p>
      </div>
      {step.done || inert ? null : (
        <Button
          asChild
          variant="outline"
          size="sm"
          className="shrink-0"
          data-testid={`onboarding-cta-${step.key}`}
        >
          <Link to={step.to}>{t(`onboarding.step.${step.key}.cta`)}</Link>
        </Button>
      )}
    </li>
  );
}
