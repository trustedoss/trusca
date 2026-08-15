// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * NotFoundPage: the catch-all for an address that matches no route.
 *
 * Before this, `*` redirected to `/login`. A signed-in user who mistyped a URL
 * was thrown out to a sign-in form that then bounced them back to the
 * dashboard, and nothing anywhere said the address was wrong. A typo read as a
 * session problem.
 *
 * The route lives inside the authenticated shell, so the sidebar and header
 * stay put and the user is still somewhere rather than nowhere. An anonymous
 * visitor never reaches it: `RequireAuth` sends them to `/login` first, which
 * keeps the SPA from telling a stranger which paths exist.
 *
 * The address is echoed back because "this page does not exist" without saying
 * which page leaves the user guessing at their own typo.
 *
 * The heading is visually hidden. `EmptyState` carries the visible sentence in
 * a `<p>`, and `AppShell` supplies no `<h1>` of its own, so without this the
 * screen would have no heading at all and a reader navigating by headings
 * would find nothing here. It says the short tab-title phrase rather than
 * repeating the sentence below it.
 */
import { FileQuestion } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link, useLocation } from "react-router-dom";

import { EmptyState } from "@/components/EmptyState";
import { Button } from "@/components/ui/button";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";

export function NotFoundPage() {
  const { t } = useTranslation("common");
  const location = useLocation();
  useDocumentTitle(t("not_found.tab_title"));

  // pathname alone would print "/reports" for "/reports?range=90d", which is
  // not the address the user is looking at.
  const attempted = location.pathname + location.search + location.hash;

  return (
    <div className="flex flex-1 items-start px-6 py-10">
      <h1 className="sr-only">{t("not_found.tab_title")}</h1>
      <EmptyState
        data-testid="not-found-page"
        icon={<FileQuestion />}
        title={t("not_found.title")}
        description={t("not_found.description")}
        action={
          <div className="flex flex-col items-center gap-4">
            <p
              className="max-w-md break-all font-mono text-xs text-muted-foreground"
              data-testid="not-found-page-path"
            >
              {t("not_found.address_label", { path: attempted })}
            </p>
            <Button asChild variant="outline" size="sm">
              <Link to="/" data-testid="not-found-page-home">
                {t("not_found.back_home")}
              </Link>
            </Button>
          </div>
        }
      />
    </div>
  );
}
