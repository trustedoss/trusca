/**
 * AdminNotFound — 404 shell used both as the catch-all under `/admin/*` and
 * as the existence-hide page rendered by `AdminLayout` when a non-super-admin
 * lands on `/admin`.
 *
 * Existence-hide rationale: the backend already returns 404 (instead of 403)
 * for non-super-admin actors hitting `/v1/admin/*`. Keeping the SPA aligned
 * means a developer poking around at `/admin` cannot tell from the response
 * shape whether the route exists.
 */
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";

export function AdminNotFound() {
  const { t } = useTranslation("admin");
  return (
    <div
      className="flex min-h-screen items-center justify-center bg-background px-6"
      data-testid="admin-not-found"
    >
      <div className="max-w-sm space-y-4 text-center">
        <h1 className="text-2xl font-semibold tracking-tight">
          {t("admin.not_found.title")}
        </h1>
        <p className="text-sm text-muted-foreground">
          {t("admin.not_found.subtitle")}
        </p>
        <Button asChild variant="outline" size="sm">
          <Link to="/">{t("admin.not_found.back_home")}</Link>
        </Button>
      </div>
    </div>
  );
}
