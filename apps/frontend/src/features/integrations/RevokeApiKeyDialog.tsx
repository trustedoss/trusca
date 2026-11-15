// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * RevokeApiKeyDialog — chore C.
 *
 * Confirmation dialog for revoking (soft-deleting) an API key.
 * Revocation is idempotent on the backend (apps/backend/api/v1/api_keys.py:194-219).
 */
import { useTranslation } from "react-i18next";

import { Button } from "@/components/primer/Button";
import { PrimerDialog } from "@/components/primer/PrimerDialog";
import type { APIKeyListItem } from "@/types/apiKey";

interface RevokeApiKeyDialogProps {
  target: APIKeyListItem | null;
  onClose: () => void;
  onConfirm: (id: string) => void;
  submitting: boolean;
}

export function RevokeApiKeyDialog({
  target,
  onClose,
  onConfirm,
  submitting,
}: RevokeApiKeyDialogProps) {
  const { t } = useTranslation("integrations");
  const open = target !== null;

  return (
    <PrimerDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title={t("api_keys.revoke_confirm.title")}
      description={t("api_keys.revoke_confirm.description")}
      width="medium"
      testId="integrations-revoke-dialog"
      footer={
        <>
          <Button
            type="button"
            variant="outline"
            onClick={onClose}
            disabled={submitting}
            data-testid="integrations-revoke-cancel"
          >
            {t("api_keys.revoke_confirm.cancel")}
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={submitting || !target}
            onClick={() => target && onConfirm(target.id)}
            data-testid="integrations-revoke-confirm"
          >
            {t("api_keys.revoke_confirm.confirm")}
          </Button>
        </>
      }
    >
      {target ? (
        <p
          className="rounded-md border bg-muted/40 px-3 py-2 font-mono text-xs"
          data-testid="integrations-revoke-target"
        >
          {target.name} <span className="text-muted-foreground">·</span>{" "}
          {target.key_prefix}…
        </p>
      ) : null}
    </PrimerDialog>
  );
}
