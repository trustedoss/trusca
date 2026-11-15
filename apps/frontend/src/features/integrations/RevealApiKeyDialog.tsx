// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * RevealApiKeyDialog — chore C.
 *
 * One-shot reveal of the plaintext API key returned from POST /v1/api-keys.
 * The backend NEVER surfaces this string again, so the UI surrounds it with
 * a strong visual warning + a copy button.
 */
import { AlertIcon, CopyIcon } from "@primer/octicons-react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/primer/alert";
import { Button } from "@/components/primer/Button";
import { PrimerDialog } from "@/components/primer/PrimerDialog";
import type { APIKeyCreateOut } from "@/types/apiKey";

interface RevealApiKeyDialogProps {
  created: APIKeyCreateOut | null;
  onClose: () => void;
  onCopy: (value: string) => void;
}

export function RevealApiKeyDialog({
  created,
  onClose,
  onCopy,
}: RevealApiKeyDialogProps) {
  const { t } = useTranslation("integrations");

  return (
    <PrimerDialog
      open={created !== null}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title={t("api_keys.create_result.title")}
      description={created?.name ?? ""}
      width="medium"
      testId="integrations-reveal-dialog"
      footer={
        <>
          <Button
            type="button"
            onClick={onClose}
            data-testid="integrations-reveal-done"
          >
            {t("api_keys.create_result.done")}
          </Button>
        </>
      }
      dismissible={false}
    >
      <Alert variant="warning">
        <AlertIcon size={16} />
        <AlertDescription>
          {t("api_keys.create_result.warning")}
        </AlertDescription>
      </Alert>

      {created ? (
        <div
          className="flex items-center gap-2 rounded-md border bg-muted/40 p-2"
          data-testid="integrations-reveal-key-block"
        >
          <code
            className="flex-1 break-all font-mono text-xs"
            data-testid="integrations-reveal-key-value"
          >
            {created.raw_key}
          </code>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => onCopy(created.raw_key)}
            data-testid="integrations-reveal-copy"
          >
            <CopyIcon size={12} />
            <span>{t("api_keys.create_result.copy")}</span>
          </Button>
        </div>
      ) : null}
    </PrimerDialog>
  );
}
