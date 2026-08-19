// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { FileUp } from "lucide-react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
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
import { Textarea } from "@/components/ui/textarea";
import type { BulkResult } from "@/features/admin/api/adminUsersApi";
import { useBulkCreateAdminUsers } from "@/features/admin/api/useAdminUserBulk";
import { parseUserImportCsv, type ImportParseError } from "@/lib/userImportCsv";
import { problemMessage } from "@/lib/problemMessage";

/**
 * Adding people in bulk (N4).
 *
 * Paste or upload the CSV the export writes. Two decisions shape the dialog:
 *
 * The file is parsed here and sent as rows, not forwarded as a file. The
 * server never sees the spreadsheet, so a reordered column is caught before
 * anything is created rather than surfacing as forty failed rows.
 *
 * And the result table lists every row, including the ones that worked. An
 * import of a few hundred people is exactly where "only failures shown" makes
 * somebody count what is missing.
 */

export interface AdminUserImportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const TEMPLATE = "email,full_name,team_id,role\n";

export function AdminUserImportDialog({
  open,
  onOpenChange,
}: AdminUserImportDialogProps) {
  const { t } = useTranslation("admin");
  const [text, setText] = useState("");
  const [parseErrors, setParseErrors] = useState<ImportParseError[]>([]);
  const [result, setResult] = useState<BulkResult | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const mutation = useBulkCreateAdminUsers();

  function reset() {
    setText("");
    setParseErrors([]);
    setResult(null);
    mutation.reset();
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function handlePick(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setText(await file.text());
    setParseErrors([]);
    setResult(null);
    mutation.reset();
  }

  async function handleSubmit() {
    const parsed = parseUserImportCsv(text);
    setParseErrors(parsed.errors);
    setResult(null);
    if (parsed.errors.length > 0 || parsed.rows.length === 0) return;
    try {
      setResult(await mutation.mutateAsync(parsed.rows));
    } catch {
      // Surfaced from mutation.error below.
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) reset();
      }}
    >
      <DialogContent
        className="max-w-2xl"
        data-testid="admin-user-import-dialog"
      >
        <DialogHeader>
          <DialogTitle>{t("admin.users.import.title")}</DialogTitle>
          <DialogDescription>{t("admin.users.import.description")}</DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => fileInputRef.current?.click()}
              data-testid="admin-user-import-pick"
            >
              <FileUp className="mr-1.5 h-4 w-4" aria-hidden="true" />
              {t("admin.users.import.choose_file")}
            </Button>
            <Input
              ref={fileInputRef}
              type="file"
              accept=".csv,text/csv"
              className="hidden"
              onChange={handlePick}
              data-testid="admin-user-import-file"
            />
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setText(TEMPLATE)}
              data-testid="admin-user-import-template"
            >
              {t("admin.users.import.insert_header")}
            </Button>
          </div>

          <Textarea
            rows={8}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={t("admin.users.import.placeholder")}
            className="font-mono text-xs"
            aria-label={t("admin.users.import.textarea_label")}
            data-testid="admin-user-import-text"
          />

          {parseErrors.length > 0 ? (
            <Alert variant="destructive" data-testid="admin-user-import-parse-errors">
              <AlertDescription>
                <ul className="flex flex-col gap-1">
                  {parseErrors.map((error) => (
                    <li key={`${error.line}-${error.reason}`}>
                      {t(`admin.users.import.parse_error.${error.reason}`, {
                        line: error.line,
                      })}
                    </li>
                  ))}
                </ul>
              </AlertDescription>
            </Alert>
          ) : null}

          {mutation.isError ? (
            <Alert variant="destructive" data-testid="admin-user-import-error">
              <AlertDescription>
                {problemMessage(mutation.error, t, {
                  action: "admin.users.import.errors.submit",
                })}
              </AlertDescription>
            </Alert>
          ) : null}

          {result ? (
            <div className="flex flex-col gap-2" data-testid="admin-user-import-result">
              <p className="text-sm">
                {t("admin.users.import.summary", {
                  succeeded: result.succeeded,
                  failed: result.failed,
                })}
              </p>
              <div className="max-h-56 overflow-y-auto rounded-md border">
                <table className="w-full text-xs">
                  <tbody>
                    {result.results.map((row) => (
                      <tr
                        key={row.index}
                        className="border-b last:border-b-0"
                        data-testid="admin-user-import-row"
                        data-status={row.status}
                      >
                        <td className="px-3 py-1.5 font-mono">{row.identifier}</td>
                        <td className="px-3 py-1.5">
                          {t(`admin.users.bulk.status.${row.status}`)}
                        </td>
                        <td className="px-3 py-1.5 text-muted-foreground">
                          {row.reason
                            ? t(`admin.users.bulk.reason.${row.reason}`, {
                                defaultValue: t("admin.users.bulk.reason.failed"),
                              })
                            : ""}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="ghost"
            onClick={() => onOpenChange(false)}
            data-testid="admin-user-import-close"
          >
            {t("admin.users.import.close")}
          </Button>
          <Button
            type="button"
            onClick={handleSubmit}
            disabled={mutation.isPending || text.trim().length === 0}
            data-testid="admin-user-import-submit"
          >
            {mutation.isPending
              ? t("admin.users.import.submitting")
              : t("admin.users.import.submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
