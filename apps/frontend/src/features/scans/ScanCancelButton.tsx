/**
 * ScanCancelButton — user-facing "Cancel scan" affordance (PR-A3).
 *
 * Reusable across the scan progress drawer (`ScanProgress`) and the global
 * scan-queue rows (`ScansPage`). Encapsulates:
 *
 *   - A guard: only renders when the scan is `queued` or `running`. Terminal
 *     scans have nothing to cancel.
 *   - An inline confirm strip (no shadcn `AlertDialog` is in the tree — the
 *     portal uses the PR #13 inline-confirm pattern, mirrored from
 *     `AdminScanDrawer`).
 *   - The `useCancelScan` mutation + a local toast for the failure modes
 *     (409 already-terminal, 404 not-found, 403 forbidden) keyed by a stable
 *     `data-toast-key` for e2e assertions.
 *
 * Accessibility:
 *   - The trigger and confirm controls are real `<button>`s with explicit
 *     `aria-label`s.
 *   - The confirm strip is `role="alertdialog"` + `aria-live="polite"` so a
 *     screen reader announces the destructive prompt.
 *   - Color is never the only signal: the destructive button carries a label
 *     and a `<XCircle>` icon.
 */
import { Loader2, XCircle } from "lucide-react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useCancelScan } from "@/features/scans/useCancelScan";
import {
  scanCancelErrorKey,
  scanCancelErrorToken,
} from "@/features/scans/scanErrorMessage";
import { cn } from "@/lib/utils";
import type { ScanStatus } from "@/lib/projectsApi";

interface ToastState {
  id: number;
  text: string;
  token: string;
}

export interface ScanCancelButtonProps {
  scanId: string;
  /** Current scan status — the trigger only shows for queued/running. */
  status: ScanStatus;
  /** Fired after the backend confirms the cancellation (status flips). */
  onCancelled?: () => void;
  /** `sm` (default) for table rows, `default` for the drawer footer. */
  size?: "sm" | "default";
  className?: string;
}

const CANCELLABLE: ReadonlySet<ScanStatus> = new Set(["queued", "running"]);

export function ScanCancelButton({
  scanId,
  status,
  onCancelled,
  size = "sm",
  className,
}: ScanCancelButtonProps) {
  const { t } = useTranslation("scans");
  const cancel = useCancelScan();
  const [confirming, setConfirming] = useState(false);
  const [toast, setToast] = useState<ToastState | null>(null);
  const toastSeq = useRef(0);

  function notify(text: string, token: string) {
    toastSeq.current += 1;
    setToast({ id: toastSeq.current, text, token });
  }

  async function handleConfirm() {
    try {
      await cancel.mutateAsync({ scanId });
      setConfirming(false);
      onCancelled?.();
    } catch (err) {
      // 409 here is benign — the scan finished between render and click. We
      // still surface it so the user knows their click did not "stick".
      notify(t(scanCancelErrorKey(err)), scanCancelErrorToken(err));
      setConfirming(false);
    }
  }

  if (!CANCELLABLE.has(status)) return null;

  return (
    <>
      {confirming ? (
        <div
          className={cn(
            "flex flex-col gap-2 rounded-md border border-risk-high/40 bg-risk-high/5 px-3 py-2 text-sm",
            className,
          )}
          data-testid="scan-cancel-confirm"
          role="alertdialog"
          aria-live="polite"
          aria-label={t("cancel.confirm.title")}
        >
          <p className="text-foreground">{t("cancel.confirm.message")}</p>
          <div className="flex justify-end gap-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setConfirming(false)}
              disabled={cancel.isPending}
              data-testid="scan-cancel-dismiss"
            >
              {t("cancel.confirm.dismiss")}
            </Button>
            <Button
              size="sm"
              variant="destructive"
              onClick={handleConfirm}
              disabled={cancel.isPending}
              data-testid="scan-cancel-confirm-ok"
              aria-label={t("cancel.confirm.confirm")}
            >
              {cancel.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              ) : null}
              {t("cancel.confirm.confirm")}
            </Button>
          </div>
        </div>
      ) : (
        <Button
          size={size}
          variant="outline"
          onClick={() => setConfirming(true)}
          data-testid="scan-cancel-button"
          data-scan-id={scanId}
          aria-label={t("cancel.action")}
          className={cn(
            // W11-F polish — the cancel CTA already inherits Button's
            // transition-all duration-fast ease-out-soft; the override here
            // only swaps colour tokens, which now follow the same timing.
            // W11-H a11y — `text-risk-high` (#ea580c) on `hover:bg-risk-high/5`
            // measured 3.0 ~ 3.6:1 (below WCAG AA 4.5:1 for body text). Swap
            // to `text-orange-800` from the same hue family — same orange
            // brand, deeper shade — for 6.47:1. Border keeps `risk-high` so
            // the warning identity stays intact.
            "gap-1 border-risk-high/40 text-orange-800 hover:bg-risk-high/5",
            className,
          )}
        >
          <XCircle className="h-3.5 w-3.5" aria-hidden />
          {t("cancel.action")}
        </Button>
      )}
      {toast ? (
        <div
          // W11-F polish — toast appears with a soft slide-up + fade so the
          // user notices it without a startle (Linear notification pattern).
          // The `key` change re-mounts the wrapper when a new toast token
          // fires, replaying the entrance animation for each error event.
          key={toast.id}
          className={cn(
            "fixed bottom-4 right-4 z-50 max-w-sm",
            "animate-in fade-in-0 slide-in-from-bottom-2 duration-base ease-out-soft",
          )}
          data-testid="scan-cancel-toast"
          data-toast-key={toast.token}
        >
          <Alert
            variant="destructive"
            className="shadow-lg"
            role="status"
            aria-live="polite"
          >
            <AlertDescription>{toast.text}</AlertDescription>
          </Alert>
        </div>
      ) : null}
    </>
  );
}
