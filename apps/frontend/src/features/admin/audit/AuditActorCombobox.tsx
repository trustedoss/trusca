// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { CheckIcon } from "@primer/octicons-react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { PaletteEmpty, PaletteItem } from "@/components/primer/CommandPalette";
import { ComboboxPopover } from "@/components/primer/ComboboxPopover";
import { listAdminUsers } from "@/features/admin/api/adminUsersApi";
import { useAdminUser } from "@/features/admin/api/useAdminUser";
import { adminUsersQueryKey } from "@/features/admin/api/useAdminUsers";
import { cn } from "@/lib/utils";

// One page is enough to pick from; past it the list says it is cut short and
// asks for a search term, which the server matches against email and name.
export const ACTOR_PICKER_PAGE_SIZE = 20;

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

interface Props {
  /** The actor id in the URL, or "" for every actor. */
  selectedId: string;
  onSelect: (userId: string | null) => void;
  /** Applied to the trigger button, so a `<Label htmlFor>` can name it. */
  triggerId?: string;
}

/**
 * Actor filter for the audit log: pick a person by name or email.
 *
 * The filter used to be a free-text box for a user UUID, which an
 * administrator only has after opening the user elsewhere and copying it.
 * Same Popover + Command recipe as `ProjectCombobox`; the list comes from
 * the admin users endpoint, so it includes deactivated accounts, whose rows
 * stay in the trail.
 *
 * The URL keeps carrying the id, so a shared link still selects the same
 * person. The trigger label is looked up from that id; an id the directory
 * no longer has (a deleted account) is shown as is.
 */
export function AuditActorCombobox({ selectedId, onSelect, triggerId }: Props) {
  const { t } = useTranslation("admin");
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
  const query = useQuery({
    queryKey: adminUsersQueryKey({
      page: 1,
      page_size: ACTOR_PICKER_PAGE_SIZE,
      search: trimmedQuery,
    }),
    queryFn: () =>
      listAdminUsers({
        page: 1,
        page_size: ACTOR_PICKER_PAGE_SIZE,
        search: trimmedQuery || null,
      }),
    enabled: open,
    staleTime: 30_000,
  });

  const selectedQuery = useAdminUser(
    UUID_RE.test(selectedId) ? selectedId : null,
  );
  const selectedLabel = selectedId
    ? (selectedQuery.data?.email ?? selectedId)
    : null;

  const items = query.data?.items ?? [];
  const isTruncated = (query.data?.total ?? 0) > items.length;

  function handleOpenChange(next: boolean): void {
    setOpen(next);
    if (!next) {
      setSearchInput("");
      setDebouncedQuery("");
    }
  }

  function handleSelect(userId: string | null): void {
    onSelect(userId);
    handleOpenChange(false);
  }

  return (
    <ComboboxPopover
      open={open}
      onOpenChange={handleOpenChange}
      triggerId={triggerId}
      triggerTestId="admin-audit-actor"
      triggerProps={{ "data-actor-id": selectedId }}
      triggerLabel={
        <span className={cn(selectedLabel ? null : "text-muted-foreground")}>
          {selectedLabel ?? t("admin.audit.filter.actor_all")}
        </span>
      }
      searchValue={searchInput}
      onSearchChange={setSearchInput}
      searchPlaceholder={t("admin.audit.filter.actor_search_placeholder")}
      searchAriaLabel={t("admin.audit.filter.actor_search_placeholder")}
      searchTestId="admin-audit-actor-search"
      listTestId="admin-audit-actor-list"
    >
      {selectedId ? (
        <PaletteItem
          value="__all__"
          data-testid="admin-audit-actor-clear"
          onSelect={() => handleSelect(null)}
        >
          <span className="ml-6 truncate">
            {t("admin.audit.filter.actor_all")}
          </span>
        </PaletteItem>
      ) : null}
      {query.isError ? (
        <div
          role="alert"
          className="py-6 text-center text-sm text-destructive"
          data-testid="admin-audit-actor-error"
        >
          {t("admin.audit.filter.actor_load_failed")}
        </div>
      ) : query.data == null ? (
        <div
          role="status"
          aria-live="polite"
          className="py-6 text-center text-sm text-muted-foreground"
          data-testid="admin-audit-actor-loading"
        >
          {t("admin.audit.filter.actor_loading")}
        </div>
      ) : items.length === 0 ? (
        <PaletteEmpty data-testid="admin-audit-actor-empty">
          {t("admin.audit.filter.actor_empty")}
        </PaletteEmpty>
      ) : (
        <>
          {items.map((user) => (
            <PaletteItem
              key={user.id}
              value={user.id}
              data-testid="admin-audit-actor-option"
              data-user-id={user.id}
              onSelect={() => handleSelect(user.id)}
            >
              <CheckIcon
                className={cn(
                  "mr-2 shrink-0",
                  selectedId === user.id ? "opacity-100" : "opacity-0",
                )}
              />
              <span className="flex min-w-0 flex-col">
                <span className="truncate">
                  {user.full_name ?? user.email}
                </span>
                <span className="truncate font-mono text-xs text-muted-foreground">
                  {user.email}
                </span>
              </span>
            </PaletteItem>
          ))}
          {isTruncated ? (
            <div
              className="px-2 py-1.5 text-xs text-muted-foreground"
              data-testid="admin-audit-actor-truncated"
            >
              {t("admin.audit.filter.actor_truncated", {
                count: ACTOR_PICKER_PAGE_SIZE,
              })}
            </div>
          ) : null}
        </>
      )}
    </ComboboxPopover>
  );
}
