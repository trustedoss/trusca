// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * CopyButton: put a value on the clipboard, and say that it happened.
 *
 * The values this product asks people to move elsewhere are the ones no one
 * retypes correctly: a CVE id into a ticket, a purl into a procurement note,
 * a CVSS vector into a scoring calculator, a webhook URL into a provider's
 * settings page. Every one of them was select-and-drag only, and the two
 * monospace ones are truncated on narrow columns, so dragging got you half a
 * string with no sign that it was half.
 *
 * Feedback goes through the same toast surface as every other confirmation
 * in the product, with a `key` so the harness can find it, rather than a
 * bespoke "Copied!" state on the button. A button that changes its own label
 * tells only the person looking at it; the toast is announced.
 *
 * The write itself lives in `lib/clipboard`, because the integrations page
 * needs the same behaviour behind its own labelled button.
 */
import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { writeToClipboard } from "@/lib/clipboard";
import { cn } from "@/lib/utils";

export interface CopyButtonProps {
  /** The exact text to place on the clipboard. */
  value: string;
  /**
   * What is being copied, already translated, e.g. "CVE id". It names the
   * button for screen readers ("Copy CVE id") and appears in the toast, so
   * a page with four copy buttons does not confirm four identical things.
   */
  label: string;
  className?: string;
  "data-testid"?: string;
}

export function CopyButton({
  value,
  label,
  className,
  "data-testid": dataTestId,
}: CopyButtonProps) {
  const { t } = useTranslation("common");
  const { toast } = useToast();
  const [copied, setCopied] = useState(false);

  async function onCopy() {
    const ok = await writeToClipboard(value);
    if (ok) {
      setCopied(true);
      // The tick is a second channel for the same fact, for someone whose
      // eyes are on the button rather than the corner of the screen. It is
      // not the only channel, so its timing does not matter much.
      window.setTimeout(() => setCopied(false), 1500);
      toast(t("copy.copied", { what: label }), {
        tone: "success",
        key: "copied",
      });
    } else {
      toast(t("copy.failed", { what: label }), {
        tone: "error",
        key: "copy_failed",
      });
    }
  }

  return (
    <Button
      type="button"
      size="sm"
      variant="ghost"
      className={cn("h-6 w-6 p-0", className)}
      onClick={(e) => {
        // These sit inside rows and drawers that open something on click.
        e.stopPropagation();
        void onCopy();
      }}
      aria-label={t("copy.label", { what: label })}
      data-testid={dataTestId}
      data-copied={copied ? "true" : undefined}
    >
      {copied ? (
        <Check className="h-3.5 w-3.5" aria-hidden />
      ) : (
        <Copy className="h-3.5 w-3.5" aria-hidden />
      )}
    </Button>
  );
}
