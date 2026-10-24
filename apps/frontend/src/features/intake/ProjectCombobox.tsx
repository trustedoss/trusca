// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronsUpDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { listProjects } from "@/lib/projectsApi";
import { cn } from "@/lib/utils";

export interface ProjectComboboxSelection {
  id: string;
  name: string;
}

// One page is enough to pick from: past it the list says it is cut short and
// asks for a search term, which the server matches against name, slug and
// Git URL across every project the caller can reach.
export const PROJECT_PICKER_PAGE_SIZE = 20;

interface Props {
  selected: ProjectComboboxSelection | null;
  onSelect: (project: ProjectComboboxSelection) => void;
  /** Applied to the trigger button, so a `<Label htmlFor>` can name it. */
  triggerId?: string;
}

/**
 * Searchable project picker for the intake request form.
 *
 * The form used to ask for a project id, which nobody filing a request has
 * to hand: they know the project by name. This follows the same Popover +
 * Command recipe as `TeamCombobox`, so keyboard handling (arrow keys, Enter,
 * Escape) comes from the primitives rather than from this file.
 *
 * The list is fetched only once the popover opens, so a page that is only
 * read does not list projects it never shows. `shouldFilter={false}`: the
 * server already filtered on the term, and cmdk's fuzzy filter would
 * re-filter against keystrokes the debounce has not sent yet.
 */
export function ProjectCombobox({ selected, onSelect, triggerId }: Props) {
  const { t } = useTranslation("intake");
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
    queryKey: [
      "projects",
      { page: 1, size: PROJECT_PICKER_PAGE_SIZE, q: trimmedQuery, picker: true },
    ],
    queryFn: () =>
      listProjects({
        page: 1,
        size: PROJECT_PICKER_PAGE_SIZE,
        q: trimmedQuery || undefined,
      }),
    enabled: open,
    staleTime: 30_000,
  });

  const items = query.data?.items ?? [];
  const isTruncated = (query.data?.total ?? 0) > items.length;

  function handleOpenChange(next: boolean): void {
    setOpen(next);
    if (!next) {
      setSearchInput("");
      setDebouncedQuery("");
    }
  }

  function handleSelect(project: ProjectComboboxSelection): void {
    onSelect(project);
    handleOpenChange(false);
  }

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <Button
          id={triggerId}
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          data-testid="intake-project"
          data-project-id={selected?.id ?? ""}
          className="w-56 justify-between font-normal"
        >
          <span
            className={cn("truncate", selected ? null : "text-muted-foreground")}
          >
            {selected ? selected.name : t("project_picker.placeholder")}
          </span>
          <ChevronsUpDown className="h-4 w-4 shrink-0 opacity-50" aria-hidden />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-72 p-0" align="start">
        <Command shouldFilter={false}>
          <CommandInput
            value={searchInput}
            onValueChange={setSearchInput}
            placeholder={t("project_picker.search_placeholder")}
            aria-label={t("project_picker.search_placeholder")}
            data-testid="intake-project-search"
          />
          <CommandList data-testid="intake-project-list">
            {query.isError ? (
              <div
                role="alert"
                className="py-6 text-center text-sm text-destructive"
                data-testid="intake-project-error"
              >
                {t("project_picker.load_failed")}
              </div>
            ) : query.data == null ? (
              <div
                role="status"
                aria-live="polite"
                className="py-6 text-center text-sm text-muted-foreground"
                data-testid="intake-project-loading"
              >
                {t("project_picker.loading")}
              </div>
            ) : items.length === 0 ? (
              <CommandEmpty data-testid="intake-project-empty">
                {t("project_picker.empty")}
              </CommandEmpty>
            ) : (
              <>
                {items.map((project) => (
                  <CommandItem
                    key={project.id}
                    value={project.id}
                    data-testid="intake-project-option"
                    data-project-id={project.id}
                    onSelect={() =>
                      handleSelect({ id: project.id, name: project.name })
                    }
                  >
                    <Check
                      className={cn(
                        "mr-2 h-4 w-4 shrink-0",
                        selected?.id === project.id ? "opacity-100" : "opacity-0",
                      )}
                      aria-hidden
                    />
                    <span className="flex min-w-0 flex-col">
                      <span className="truncate">{project.name}</span>
                      <span className="truncate font-mono text-xs text-muted-foreground">
                        {project.slug}
                      </span>
                    </span>
                  </CommandItem>
                ))}
                {isTruncated ? (
                  <div
                    className="px-2 py-1.5 text-xs text-muted-foreground"
                    data-testid="intake-project-truncated"
                  >
                    {t("project_picker.truncated", {
                      count: PROJECT_PICKER_PAGE_SIZE,
                    })}
                  </div>
                ) : null}
              </>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
