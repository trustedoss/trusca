// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { BookmarkPlus } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Alert, AlertDescription } from "@/components/ui/alert";
import type { SearchKind } from "@/features/search/api/searchResultsApi";
import {
  useCreateSavedSearch,
  useSavedSearches,
} from "@/features/search/api/useSearchResults";
import { problemMessage } from "@/lib/problemMessage";

/**
 * Park the current search under a name.
 *
 * What gets saved is the page's whole query string, replayed verbatim on open.
 * The server treats it as opaque, so a filter added to the page later is
 * savable the day it ships without a schema change on either side.
 *
 * The per-user cap comes back on the list response, so the button can go
 * disabled before a save fails rather than after.
 */

export interface SaveSearchButtonProps {
  kind: SearchKind;
  params: Record<string, string>;
  disabled?: boolean;
}

export function SaveSearchButton({
  kind,
  params,
  disabled = false,
}: SaveSearchButtonProps) {
  const { t } = useTranslation("search");
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const saved = useSavedSearches();
  const create = useCreateSavedSearch();

  const atLimit =
    saved.data != null && saved.data.total >= saved.data.limit;

  function handleSave() {
    create.mutate(
      { name: name.trim(), kind, params },
      {
        onSuccess: () => {
          setOpen(false);
          setName("");
        },
      },
    );
  }

  return (
    <>
      <Button
        size="sm"
        variant="outline"
        disabled={disabled || atLimit}
        title={atLimit ? t("save.at_limit", { limit: saved.data?.limit }) : undefined}
        onClick={() => setOpen(true)}
        data-testid="search-save-trigger"
      >
        <BookmarkPlus className="mr-1.5 h-4 w-4" aria-hidden />
        {t("save.trigger")}
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent data-testid="search-save-dialog">
          <DialogHeader>
            <DialogTitle>{t("save.title")}</DialogTitle>
            <DialogDescription>{t("save.subtitle")}</DialogDescription>
          </DialogHeader>

          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder={t("save.name_placeholder")}
            maxLength={60}
            data-testid="search-save-name"
          />

          {create.isError ? (
            <Alert variant="destructive" data-testid="search-save-error">
              <AlertDescription>
                {problemMessage(create.error, t, { action: "save.failed" })}
              </AlertDescription>
            </Alert>
          ) : null}

          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              {t("save.cancel")}
            </Button>
            <Button
              onClick={handleSave}
              disabled={name.trim().length === 0 || create.isPending}
              data-testid="search-save-confirm"
            >
              {t("save.confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
