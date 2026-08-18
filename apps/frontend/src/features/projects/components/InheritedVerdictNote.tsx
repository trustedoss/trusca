// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Says when a component's answer came from the organization, not this team.
 *
 * Without it an inherited answer is indistinguishable from a local one, and
 * the two need different responses: a team that disagrees with its own
 * decision reopens it, and a team that disagrees with an inherited one has to
 * decide locally instead, which is the thing this note tells them they can do.
 *
 * Renders nothing when the answer is the project's own or when nobody has
 * decided. A row saying "your team decided this" beside a status the screen
 * already shows is noise, and this panel sits inside a drawer where every line
 * competes with the component's own detail.
 */
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { useEffectiveVerdict } from "@/features/approvals/useOrganizationVerdicts";

interface InheritedVerdictNoteProps {
  projectId: string;
  /** The package id, not the version: rulings are about the package. */
  componentId: string;
}

export function InheritedVerdictNote({
  projectId,
  componentId,
}: InheritedVerdictNoteProps) {
  const { t } = useTranslation("project_detail");
  const query = useEffectiveVerdict(projectId, componentId);

  const resolved = query.data;
  if (!resolved || resolved.scope !== "organization" || !resolved.status) {
    return null;
  }

  return (
    <section
      className="rounded-lg border p-3 text-xs"
      data-testid="component-inherited-verdict"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="info">{t("drawer.verdict.inherited_badge")}</Badge>
        <span className="text-muted-foreground">
          {t(`drawer.verdict.status.${resolved.status}`)}
        </span>
      </div>
      <p className="pt-2 text-muted-foreground">
        {t("drawer.verdict.inherited_explanation")}
      </p>
      {resolved.justification ? (
        <p
          className="pt-2 break-words"
          data-testid="component-inherited-verdict-reason"
        >
          {resolved.justification}
        </p>
      ) : null}
    </section>
  );
}
