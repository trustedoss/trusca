/**
 * RevealApiKeyDialog — chore C.
 *
 * One-shot reveal of the plaintext API key returned from POST /v1/api-keys.
 * The backend NEVER surfaces this string again, so the UI surrounds it with
 * a strong visual warning + a copy button.
 */
import { AlertTriangle, Copy } from "lucide-react";
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
    <Dialog
      open={created !== null}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent
        data-testid="integrations-reveal-dialog"
        // Hard-disable closing via overlay click / ESC so the user must
        // explicitly acknowledge the warning before dismissing the only
        // surface that ever displays the plaintext.
        onPointerDownOutside={(e) => e.preventDefault()}
        onEscapeKeyDown={(e) => e.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>{t("api_keys.create_result.title")}</DialogTitle>
          <DialogDescription>{created?.name ?? ""}</DialogDescription>
        </DialogHeader>

        <Alert
          variant="destructive"
          className="border-amber-300 bg-amber-50 text-amber-900"
        >
          <AlertTriangle className="h-4 w-4" aria-hidden />
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
              <Copy className="h-3 w-3" aria-hidden />
              <span>{t("api_keys.create_result.copy")}</span>
            </Button>
          </div>
        ) : null}

        <DialogFooter>
          <Button
            type="button"
            onClick={onClose}
            data-testid="integrations-reveal-done"
          >
            {t("api_keys.create_result.done")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
