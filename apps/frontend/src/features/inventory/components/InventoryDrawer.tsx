// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useComponentUsage } from "@/features/inventory/api/useInventory";
import { COMPONENTS_SEARCH_PARAM } from "@/features/projects/components/tabSearchParam";
import { problemMessage } from "@/lib/problemMessage";

export interface InventoryDrawerProps {
  /** `null` closes the drawer. Comes from `?inv_component=` so reload restores it. */
  componentId: string | null;
  onClose: () => void;
}

/**
 * Reverse lookup: which projects use the selected package, at which version.
 *
 * This is the payoff of the inventory page — the list answers "do we use it",
 * the drawer answers "where, and what would I have to touch". Each row links
 * into that project's Components tab pre-filtered to the package, so the next
 * step is one click rather than a manual search.
 */
export function InventoryDrawer({ componentId, onClose }: InventoryDrawerProps) {
  const { t } = useTranslation("inventory");
  const usage = useComponentUsage(componentId);
  const open = componentId != null && componentId.length > 0;
  const items = usage.data?.items ?? [];

  return (
    <Sheet open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <SheetContent
        side="right"
        className="w-full sm:max-w-xl"
        data-testid="inventory-drawer"
      >
        <SheetHeader>
          <SheetTitle>{t("drawer.title")}</SheetTitle>
          <SheetDescription>{t("drawer.subtitle")}</SheetDescription>
        </SheetHeader>

        {usage.isLoading ? (
          <div className="mt-4 flex flex-col gap-2" data-testid="inventory-drawer-loading">
            {Array.from({ length: 4 }).map((_, index) => (
              <Skeleton key={index} className="h-10 w-full" />
            ))}
          </div>
        ) : null}

        {usage.isError ? (
          <Alert
            variant="destructive"
            className="mt-4"
            data-testid="inventory-drawer-error"
          >
            <AlertDescription>
              {problemMessage(usage.error, t, {
                action: "errors.load_failed",
              })}
            </AlertDescription>
          </Alert>
        ) : null}

        {!usage.isLoading && !usage.isError ? (
          <ul
            className="mt-4 flex flex-col divide-y"
            data-testid="inventory-drawer-list"
            data-total={usage.data?.total ?? 0}
          >
            {items.map((row) => (
              <li
                key={`${row.project_id}-${row.version}`}
                className="flex items-center justify-between gap-3 py-3"
                data-testid="inventory-drawer-row"
                data-project-id={row.project_id}
              >
                <span className="flex min-w-0 flex-col">
                  <Link
                    to={`/projects/${row.project_id}?tab=components&${COMPONENTS_SEARCH_PARAM}=${encodeURIComponent(row.version)}`}
                    className="truncate font-medium underline-offset-4 hover:underline"
                  >
                    {row.project_name}
                  </Link>
                  <span className="truncate font-mono text-xs text-muted-foreground">
                    {row.version}
                  </span>
                </span>
                <Badge>
                  {row.direct ? t("drawer.direct") : t("drawer.transitive")}
                </Badge>
              </li>
            ))}
          </ul>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
