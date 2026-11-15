// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { CheckIcon } from "@primer/octicons-react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { PaletteEmpty, PaletteItem } from "@/components/primer/CommandPalette";
import { ComboboxPopover } from "@/components/primer/ComboboxPopover";
import { listGroups } from "@/features/groups/api/groupsApi";
import { cn } from "@/lib/utils";
import type { TeamMembership } from "@/stores/authStore";

export interface TeamComboboxSelection {
  id: string;
  name: string;
}

// group-hierarchy Phase 6 security review: mirrors the backend's own floor
// (services.group_directory_service._MIN_SEARCH_QUERY_LEN) so this component
// never fires a request the server would just soft-fail to an empty page --
// the UI's "type more" state and the server's floor stay in sync by
// construction, the same way CommandMenu.tsx's SEARCH_MIN_CHARS mirrors
// services.search_service.MIN_QUERY_LEN.
const MIN_SEARCH_QUERY_LEN = 2;

export interface TeamComboboxProps {
  /** The caller's DIRECT-membership teams -- the default (no-search) list. */
  teams: TeamMembership[];
  /** The currently picked group/team, or `null` when nothing is selected yet. */
  selected: TeamComboboxSelection | null;
  onSelect: (team: TeamComboboxSelection) => void;
  placeholder: string;
  /** Applied to the trigger button, so a `<Label htmlFor>` can target it. */
  triggerId?: string;
}

/**
 * TeamCombobox - searchable group picker for project creation.
 *
 * group-hierarchy Phase 6: the flat `<select>` this replaces could only ever
 * list `user.teams` (the caller's DIRECT memberships), so a user who reaches
 * a group only through the permission cascade (their own membership sits on
 * an ANCESTOR of that group) had no way to target it from this form, despite
 * the backend (`can_access_group`) already authorizing it identically to a
 * direct membership. Typing here calls `GET /v1/groups?q=` (`listGroups`),
 * which already spans the caller's whole accessible set, cascade included --
 * this component adds no new backend capability, just a way to reach it.
 *
 * Two independent lists share one popover, matching the shadcn "Combobox"
 * recipe (`Popover` anchoring the existing `Command` primitives):
 *   - Search box empty: `teams`, rendered with zero extra requests --
 *     preserves the old default's zero-latency feel.
 *   - Search box non-empty (debounced ~300ms -- this codebase's usual figure,
 *     see `GroupListPage`/`SearchPage`'s own `useRef`+`setTimeout` pattern):
 *     the live `listGroups({ q })` result set, replacing the shown list, once
 *     the debounced term reaches `MIN_SEARCH_QUERY_LEN` -- below that, a
 *     "keep typing" hint instead of firing a request the server would only
 *     soft-fail to an empty page anyway (security review: this endpoint had
 *     no rate limit or query-length floor before this component turned it
 *     into a per-keystroke caller; both now live server-side too, see
 *     `core.config.group_search_rate_limit` /
 *     `services.group_directory_service._MIN_SEARCH_QUERY_LEN`).
 *
 * `shouldFilter={false}` on the `Command` root: cmdk's own fuzzy filter
 * would otherwise re-filter whichever list is currently shown against the
 * raw keystrokes, fighting the debounce and the server-side match this
 * component already relies on.
 */
export function TeamCombobox({
  teams,
  selected,
  onSelect,
  placeholder,
  triggerId,
}: TeamComboboxProps) {
  const { t } = useTranslation("projects");
  const [open, setOpen] = useState(false);
  const [searchInput, setSearchInput] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setDebouncedQuery(searchInput), 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [searchInput]);

  const trimmedQuery = debouncedQuery.trim();
  const hasTypedQuery = trimmedQuery.length > 0;
  const isSearching = trimmedQuery.length >= MIN_SEARCH_QUERY_LEN;
  const isBelowMinLength = hasTypedQuery && !isSearching;

  const searchQuery = useQuery({
    queryKey: ["groups", { q: trimmedQuery }],
    queryFn: () => listGroups({ q: trimmedQuery }),
    enabled: isSearching,
    staleTime: 30_000,
  });

  const searchResults = searchQuery.data?.items ?? [];
  const isSearchLoading =
    isSearching && searchQuery.isFetching && searchQuery.data == null;

  // Reset the search box on close, so the next open starts from the
  // zero-latency default list rather than restoring a stale term.
  function handleOpenChange(next: boolean): void {
    setOpen(next);
    if (!next) {
      setSearchInput("");
      setDebouncedQuery("");
    }
  }

  function handleSelect(team: TeamComboboxSelection): void {
    onSelect(team);
    handleOpenChange(false);
  }

  return (
    <ComboboxPopover
      open={open}
      onOpenChange={handleOpenChange}
      triggerId={triggerId}
      triggerTestId="project-team-combobox-trigger"
      triggerLabel={selected ? selected.name : placeholder}
      searchValue={searchInput}
      onSearchChange={setSearchInput}
      searchPlaceholder={t("create.team_combobox_search_placeholder")}
      searchTestId="project-team-combobox-search"
      listTestId="project-team-combobox-list"
    >
      {isBelowMinLength ? (
        <div
          className="py-6 text-center text-sm text-muted-foreground"
          data-testid="project-team-combobox-min-length-hint"
        >
          {t("create.team_combobox_min_length_hint")}
        </div>
      ) : isSearching ? (
        isSearchLoading ? (
          <div
            role="status"
            aria-live="polite"
            className="py-6 text-center text-sm text-muted-foreground"
            data-testid="project-team-combobox-loading"
          >
            {t("create.team_combobox_searching")}
          </div>
        ) : searchResults.length === 0 ? (
          <PaletteEmpty data-testid="project-team-combobox-empty">
            {t("create.team_combobox_empty")}
          </PaletteEmpty>
        ) : (
          searchResults.map((group) => (
            <PaletteItem
              key={group.id}
              value={group.id}
              data-testid="project-team-combobox-option"
              data-group-id={group.id}
              onSelect={() => handleSelect({ id: group.id, name: group.name })}
            >
              <CheckIcon
                className={cn(
                  "mr-2 shrink-0",
                  selected?.id === group.id ? "opacity-100" : "opacity-0",
                )}
              />
              <span className="flex min-w-0 flex-col">
                <span className="truncate">{group.name}</span>
                <span className="truncate font-mono text-xs text-muted-foreground">
                  {group.slug}
                </span>
              </span>
            </PaletteItem>
          ))
        )
      ) : teams.length === 0 ? (
        <PaletteEmpty data-testid="project-team-combobox-empty">
          {t("create.team_combobox_empty")}
        </PaletteEmpty>
      ) : (
        teams.map((team) => (
          <PaletteItem
            key={team.id}
            value={team.id}
            data-testid="project-team-combobox-option"
            data-group-id={team.id}
            onSelect={() => handleSelect({ id: team.id, name: team.name })}
          >
            <CheckIcon
              className={cn(
                "mr-2 shrink-0",
                selected?.id === team.id ? "opacity-100" : "opacity-0",
              )}
            />
            <span className="truncate">{team.name}</span>
          </PaletteItem>
        ))
      )}
    </ComboboxPopover>
  );
}
