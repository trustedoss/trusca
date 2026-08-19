// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import type { ObligationFulfilmentStatus } from "@/lib/obligationConstants";

/**
 * How far along one obligation is, as a chip (N15).
 *
 * `null` renders its own label rather than nothing, because an empty cell in a
 * list of obligations reads as a rendering fault. It also renders differently
 * from "not started": nobody having looked and somebody having looked and not
 * begun are different answers, and the whole point of the record is to tell
 * them apart.
 *
 * Colour is not the only signal. Every state carries its own words, so the
 * chip survives being read in greyscale or by a screen reader.
 */

const TONE: Record<ObligationFulfilmentStatus, "success" | "info" | "none"> = {
  not_started: "none",
  in_progress: "info",
  done: "success",
  // Deliberately uncoloured. It is a decision, not progress, and tinting it
  // green would let a wall of "does not apply to us" read as work finished.
  not_applicable: "none",
};

export interface ObligationFulfilmentBadgeProps {
  status: ObligationFulfilmentStatus | null;
}

export function ObligationFulfilmentBadge({
  status,
}: ObligationFulfilmentBadgeProps) {
  const { t } = useTranslation("project_detail");
  if (status === null) {
    return (
      <Badge
        variant="muted"
        data-testid="obligation-fulfilment-badge"
        data-status="none"
      >
        {t("obligations.fulfilment.status.unrecorded")}
      </Badge>
    );
  }
  return (
    <Badge
      tone={TONE[status]}
      data-testid="obligation-fulfilment-badge"
      data-status={status}
    >
      {t(`obligations.fulfilment.status.${status}`)}
    </Badge>
  );
}
