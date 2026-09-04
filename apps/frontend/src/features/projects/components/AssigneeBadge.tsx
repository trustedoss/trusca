// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";

/**
 * AssigneeBadge - ER28b.
 *
 * Says which of three states a finding's ownership is in. The third is the
 * reason this exists at all.
 *
 *   null   unassigned      visibly waiting for somebody
 *   true   assigned        somebody has it and can act
 *   false  cannot act      somebody has it and their account is deactivated
 *
 * The third state used to be indistinguishable from the second: the row
 * carried a name, so it read as being handled, while nobody could pick it up.
 * That is worse than unassigned, because an unassigned row at least looks
 * unowned. `services.assignee` says as much in its own docstring, and only
 * enforced it when the assignment was written; this is the reading half.
 *
 * Why the comparisons are explicit
 * --------------------------------
 * `!isActive` is true for BOTH `null` and `false`, so the natural way to
 * write this folds "nobody has this" together with "somebody who cannot act
 * has this" and recreates the confusion the field was added to remove. The
 * branches therefore compare against `null` / `true` / `false` by identity,
 * and a test drives all three separately.
 *
 * Colour is never the only signal (CLAUDE.md design system + WCAG): each
 * state carries its own label, and the tones come from existing Badge
 * variants rather than new tokens.
 */
export interface AssigneeBadgeProps {
  /** `assignee_user_id`; `null` means nobody owns the finding. */
  assigneeUserId: string | null;
  /** `assignee_is_active`; `null` when unassigned. */
  assigneeIsActive: boolean | null;
  /** The signed-in user, so their own findings read as "you". */
  currentUserId?: string | null;
  "data-testid"?: string;
}

export function AssigneeBadge({
  assigneeUserId,
  assigneeIsActive,
  currentUserId = null,
  "data-testid": testId = "vulnerability-assignee-badge",
}: AssigneeBadgeProps) {
  const { t } = useTranslation("project_detail");

  // Unassigned. Checked first and by identity: `assigneeUserId == null` is the
  // authority on "nobody has this", and `assigneeIsActive` is null here too.
  if (assigneeUserId === null) {
    return (
      <Badge variant="muted" tone="none" data-testid={testId} data-state="unassigned">
        {t("vulnerabilities.assignee.unassigned")}
      </Badge>
    );
  }

  // Assigned to somebody who cannot act. `=== false` and not `!assigneeIsActive`:
  // the latter would also catch the unassigned case above if the order ever
  // changed, and order is a poor thing to depend on for a distinction this one.
  if (assigneeIsActive === false) {
    return (
      <Badge variant="outline" tone="high" data-testid={testId} data-state="inactive">
        {t("vulnerabilities.assignee.inactive")}
      </Badge>
    );
  }

  const isMine = currentUserId !== null && assigneeUserId === currentUserId;
  return (
    <Badge variant="secondary" tone="info" data-testid={testId} data-state="assigned">
      {isMine
        ? t("vulnerabilities.assignee.mine")
        : t("vulnerabilities.assignee.assigned")}
    </Badge>
  );
}
