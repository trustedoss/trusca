// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { Bookmark, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { SavedSearch } from "@/features/search/api/searchResultsApi";
import {
  useDeleteSavedSearch,
  useSavedSearches,
} from "@/features/search/api/useSearchResults";

/**
 * The dashboard's saved-searches card.
 *
 * Owns its own query rather than reading a field off a dashboard aggregate —
 * every other panel on this page fans out to its own endpoint, and a saved
 * search changing should not invalidate the whole dashboard.
 *
 * Renders nothing when the user has none. An empty card explaining a feature
 * they have not used is an ad, not information; the search page is where they
 * will meet it.
 */

function href(row: SavedSearch): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(row.params)) {
    if (value == null) continue;
    query.set(key, String(value));
  }
  // The kind is authoritative on the row even if the saved params disagree —
  // params are opaque and could predate a rename.
  query.set("kind", row.kind);
  return `/search?${query.toString()}`;
}

export function SavedSearchesPanel() {
  const { t } = useTranslation("search");
  const saved = useSavedSearches();
  const remove = useDeleteSavedSearch();

  // Nothing at all while loading — not even a skeleton. Most users have no
  // saved searches, so a placeholder would flash a card into the dashboard and
  // then take it away again, which reads as a glitch rather than as loading.
  // The panel is supplementary; it can afford to simply appear when it has
  // something to say.
  const items = saved.data?.items ?? [];
  if (saved.isLoading || saved.isError || items.length === 0) return null;

  // The section wrapper lives here rather than in DashboardPage so that a user
  // with no saved searches gets no empty element and no stray 16px of padding —
  // the dashboard is byte-identical to what it was before this panel existed.
  return (
    <section className="px-6 pt-4">
      <Card data-testid="saved-searches-panel">
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Bookmark className="h-4 w-4" aria-hidden />
            <span>{t("saved.title")}</span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="flex flex-wrap gap-2" data-total={items.length}>
            {items.map((row) => (
              <li
                key={row.id}
                className="flex items-center gap-1 rounded-full border px-2 py-1 text-sm"
                data-testid="saved-search-chip"
                data-kind={row.kind}
              >
                <Link
                  to={href(row)}
                  className="underline-offset-4 hover:underline"
                >
                  {row.name}
                </Link>
                <Badge className="px-1 py-0 text-[10px]">
                  {t(`tab.${row.kind}`)}
                </Badge>
                <button
                  type="button"
                  aria-label={t("saved.remove", { name: row.name })}
                  onClick={() => remove.mutate(row.id)}
                  data-testid="saved-search-remove"
                  className="rounded-full p-0.5 text-muted-foreground transition-colors duration-fast hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <X className="h-3 w-3" aria-hidden />
                </button>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </section>
  );
}
