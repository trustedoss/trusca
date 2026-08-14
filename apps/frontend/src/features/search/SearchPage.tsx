// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { Search as SearchIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";

import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  SEARCH_KINDS,
  type SearchKind,
} from "@/features/search/api/searchResultsApi";
import {
  SEARCH_MIN_CHARS,
  SEARCH_PAGE_SIZE,
  useSearchResults,
} from "@/features/search/api/useSearchResults";
import { SearchFacetBar } from "@/features/search/components/SearchFacetBar";
import { SearchResultsTable } from "@/features/search/components/SearchResultsTable";
import { SaveSearchButton } from "@/features/search/components/SaveSearchButton";
import { problemMessage } from "@/lib/problemMessage";

/**
 * SearchPage — the full search surface behind the ⌘K palette (S3).
 *
 * The palette answers "a few of everything, now". This answers "all of one
 * thing, paged, with counts I can filter by" — which is why it talks to a
 * different endpoint rather than a mode of the palette's.
 *
 * Everything that shapes the result set lives in the URL: the term, the tab,
 * the page, and the facet selections. That is what makes a search shareable,
 * reloadable, and — the point of this phase — savable, since a saved search is
 * just this query string parked under a name.
 *
 * Facets sit in a bar above the results rather than a left rail. The design
 * system's rule is "filters inline at the top, no modals", and every other
 * filtered surface here follows it; a left rail would be the only one.
 */

/**
 * Which scans each tab draws from.
 *
 * The asymmetry belongs to the backend, not to this display: projects and
 * components search across every scan a project has ever had, because "is this
 * package anywhere in our history" is a legitimate question; vulnerabilities
 * and licences resolve to each project's current scan, because a CVE fixed two
 * releases ago should not reappear in a triage list. `search_results_service`
 * writes the rule down, and until now that was the only place it existed.
 *
 * Stating it on screen is what keeps this page's Components tab from reading as
 * a broken copy of the `/components` inventory — the inventory shows one row
 * per package from the latest scan, this shows one per (project, version)
 * across all of them, and the row counts differ for that reason alone.
 */
const KIND_SCOPE: Record<SearchKind, "all_scans" | "current_scan"> = {
  projects: "all_scans",
  components: "all_scans",
  vulnerabilities: "current_scan",
  licenses: "current_scan",
};

function parseKind(raw: string | null): SearchKind {
  return raw && (SEARCH_KINDS as readonly string[]).includes(raw)
    ? (raw as SearchKind)
    : "components";
}

function parseList(raw: string | null): string[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((value) => value.trim())
    .filter((value) => value.length > 0);
}

function parsePage(raw: string | null): number {
  const parsed = raw ? Number.parseInt(raw, 10) : 1;
  return Number.isFinite(parsed) && parsed >= 1 ? parsed : 1;
}

export function SearchPage() {
  const { t } = useTranslation("search");
  const [searchParams, setSearchParams] = useSearchParams();

  const kind = parseKind(searchParams.get("kind"));
  const page = parsePage(searchParams.get("page"));
  const severity = parseList(searchParams.get("severity"));
  const status = parseList(searchParams.get("status"));
  const packageType = parseList(searchParams.get("package_type"));
  const licenseCategory = parseList(searchParams.get("license_category"));

  const [query, setQuery] = useState(() => searchParams.get("q") ?? "");
  const [debouncedQuery, setDebouncedQuery] = useState(query);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setDebouncedQuery(query), 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query]);

  // The debounced term is the one that reaches the URL, so typing does not
  // spam history entries.
  useEffect(() => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (debouncedQuery) next.set("q", debouncedQuery);
        else next.delete("q");
        return next;
      },
      { replace: true },
    );
  }, [debouncedQuery, setSearchParams]);

  const setParam = useCallback(
    (key: string, value: string | null) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value) next.set(key, value);
          else next.delete(key);
          // Any change to what is being searched invalidates the page number —
          // staying on page 4 of a set that just shrank shows an empty table.
          if (key !== "page") next.delete("page");
          return next;
        },
        { replace: false },
      );
    },
    [setSearchParams],
  );

  const changeKind = useCallback(
    (next: string) => {
      setSearchParams(
        (prev) => {
          const out = new URLSearchParams(prev);
          out.set("kind", next);
          // Facets are per-kind — severity means nothing on the projects tab —
          // so switching sheds them rather than carrying a filter the new tab
          // cannot show the user.
          for (const key of [
            "page",
            "severity",
            "status",
            "package_type",
            "license_category",
          ]) {
            out.delete(key);
          }
          return out;
        },
        { replace: false },
      );
    },
    [setSearchParams],
  );

  const params = useMemo(
    () => ({
      kind,
      q: debouncedQuery.trim(),
      page,
      size: SEARCH_PAGE_SIZE,
      severity,
      status,
      packageType,
      licenseCategory,
    }),
    [
      kind,
      debouncedQuery,
      page,
      severity.join(","),
      status.join(","),
      packageType.join(","),
      licenseCategory.join(","),
    ],
  );

  const results = useSearchResults(params);
  const data = results.data;
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / SEARCH_PAGE_SIZE));
  const belowThreshold = debouncedQuery.trim().length < SEARCH_MIN_CHARS;

  return (
    <div className="flex min-h-screen flex-col" data-testid="search-page">
      <PageHeader
        variant="bar"
        title={t("page.title")}
        actions={
          <SaveSearchButton
            kind={kind}
            params={Object.fromEntries(searchParams.entries())}
            disabled={belowThreshold}
          />
        }
      />

      <div className="border-b px-6 py-3">
        <label htmlFor="search-input" className="text-xs text-muted-foreground">
          {t("toolbar.search_label")}
        </label>
        <Input
          id="search-input"
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t("toolbar.search_placeholder")}
          data-testid="search-input"
          className="mt-1 h-9 max-w-xl"
        />
      </div>

      <Tabs value={kind} onValueChange={changeKind}>
        <div className="border-b px-6">
          <TabsList data-testid="search-tabs">
            {SEARCH_KINDS.map((value) => (
              <TabsTrigger
                key={value}
                value={value}
                data-testid={`search-tab-${value}`}
              >
                {t(`tab.${value}`)}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>
      </Tabs>

      <SearchFacetBar
        kind={kind}
        facets={data?.facets ?? {}}
        severity={severity}
        status={status}
        packageType={packageType}
        licenseCategory={licenseCategory}
        onChange={(key, values) =>
          setParam(key, values.length ? values.join(",") : null)
        }
      />

      <div
        className="border-b px-6 py-2 text-sm text-muted-foreground"
        data-testid="search-summary"
        data-total={total}
        data-kind={kind}
      >
        <span>
          {belowThreshold
            ? t("summary.type_more", { min: SEARCH_MIN_CHARS })
            : t("summary.count", { total })}
        </span>
        {" · "}
        {/* Shown before a term is typed too, so switching tabs is when the
            difference registers rather than after the counts confuse someone. */}
        <span data-testid="search-scope" data-scope={KIND_SCOPE[kind]}>
          {t(`scope.${KIND_SCOPE[kind]}`)}
        </span>
      </div>

      <main className="flex flex-1 flex-col">
        {results.isError ? (
          <div className="px-6 py-6">
            <Alert variant="destructive" data-testid="search-error">
              <AlertDescription>
                {problemMessage(results.error, t, {
                  action: "errors.load_failed",
                })}
              </AlertDescription>
            </Alert>
          </div>
        ) : null}

        {results.isLoading && !belowThreshold ? (
          <div className="flex flex-col gap-2 px-6 py-4" data-testid="search-loading">
            {Array.from({ length: 5 }).map((_, index) => (
              <Skeleton key={index} className="h-9 w-full" />
            ))}
          </div>
        ) : null}

        {!results.isLoading && !results.isError && total === 0 ? (
          <EmptyState
            data-testid="search-empty"
            className="m-6"
            icon={<SearchIcon />}
            title={
              belowThreshold ? t("empty.start.title") : t("empty.none.title")
            }
            description={
              belowThreshold
                ? t("empty.start.subtitle", { min: SEARCH_MIN_CHARS })
                : t("empty.none.subtitle")
            }
          />
        ) : null}

        {data && total > 0 ? <SearchResultsTable page={data} /> : null}
      </main>

      {total > SEARCH_PAGE_SIZE ? (
        <footer
          className="flex shrink-0 items-center justify-between border-t bg-card px-6 py-2 text-xs"
          data-testid="search-pagination"
        >
          <span className="text-muted-foreground">
            {t("pagination.summary", { page, total: totalPages })}
          </span>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={page <= 1}
              onClick={() => setParam("page", String(page - 1))}
              data-testid="search-page-prev"
            >
              {t("pagination.previous")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={page >= totalPages}
              onClick={() => setParam("page", String(page + 1))}
              data-testid="search-page-next"
            >
              {t("pagination.next")}
            </Button>
          </div>
        </footer>
      ) : null}
    </div>
  );
}
