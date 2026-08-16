// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * "Export CSV", for any table that has one (B5).
 *
 * Three tables gained this at once, and the parts worth sharing are the ones
 * a per-toolbar copy would get subtly different: the in-flight state, the
 * toast on success, and above all what to say when the server refuses.
 *
 * The refusal matters more than it looks. An export is declined when the
 * filtered set is too large, and the only useful response is "narrow the
 * filter", which requires naming the table, because the reader has several
 * open. So each caller passes the RFC 7807 extension its endpoint sends and
 * the message key that belongs to it, and a failure that carries neither
 * still lands on a real sentence rather than a blank toast.
 */
import { Download } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { problemMessage } from "@/lib/problemMessage";
import { ProblemError } from "@/lib/problem";

export interface ExportCsvButtonProps {
  /** Performs the download. Rejects with a `ProblemError` on refusal. */
  onExport: () => Promise<void>;
  /**
   * The problem extension this table's endpoint sets when it declines,
   * e.g. `"vulnerabilities_export_too_large"`.
   */
  tooLargeExtension: string;
  /** Message key for that refusal, in the caller's namespace. */
  tooLargeMessageKey: string;
  /** i18n namespace the caller's keys live in. */
  namespace: string;
  disabled?: boolean;
  /**
   * Why the button is off, shown on hover and to assistive tech.
   *
   * A disabled control with no reason is a dead end; the reader cannot tell
   * whether the feature is missing, broken, or waiting on something they
   * control.
   */
  disabledReason?: string;
  "data-testid": string;
}

export function ExportCsvButton({
  onExport,
  tooLargeExtension,
  tooLargeMessageKey,
  namespace,
  disabled,
  disabledReason,
  "data-testid": testId,
}: ExportCsvButtonProps) {
  const { t } = useTranslation(namespace);
  const { t: tCommon } = useTranslation("common");
  const { toast } = useToast();
  const [exporting, setExporting] = useState(false);

  async function handleClick() {
    setExporting(true);
    try {
      await onExport();
      toast(tCommon("export.started"), { tone: "success", key: "csv_started" });
    } catch (err) {
      const tooLarge =
        err instanceof ProblemError &&
        err.problem?.[tooLargeExtension] === true;
      toast(
        tooLarge
          ? t(tooLargeMessageKey)
          : // No scoped prefix: an export fails for the same reasons any
            // request fails, and the shared wording already says each of
            // them well. The action names what was being attempted so the
            // sentence has a subject.
            problemMessage(err, tCommon, {
              action: "export.failed",
              allowDetailFallback: false,
            }),
        {
          tone: "error",
          key: tooLarge ? tooLargeExtension : "export_failed",
        },
      );
    } finally {
      setExporting(false);
    }
  }

  // Blocked by the caller, for a reason the caller can state. Kept
  // focusable via `aria-disabled` rather than the `disabled` attribute: a
  // disabled button leaves the tab order entirely, so a keyboard user never
  // reaches it and never hears why it cannot be used. The reason is bound
  // with `aria-describedby`, which a screen reader announces after the
  // button's own name.
  const blocked = Boolean(disabled && disabledReason);
  const reasonId = `${testId}-reason`;

  return (
    <>
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={blocked ? undefined : handleClick}
        // The in-flight case genuinely disables: there is nothing to explain
        // and nothing useful a second press could do.
        disabled={exporting || (Boolean(disabled) && !blocked)}
        aria-disabled={blocked || undefined}
        aria-describedby={blocked ? reasonId : undefined}
        title={blocked ? disabledReason : undefined}
        // Not `pointer-events: none`, or the title would never show.
        className={blocked ? "opacity-50" : undefined}
        data-testid={testId}
        data-exporting={exporting ? "true" : undefined}
      >
        <Download className="h-4 w-4" aria-hidden />
        {tCommon("export.csv")}
      </Button>
      {blocked ? (
        <span id={reasonId} className="sr-only">
          {disabledReason}
        </span>
      ) : null}
    </>
  );
}
