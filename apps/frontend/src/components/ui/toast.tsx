/**
 * Toast — W12-B global toast provider.
 *
 * Before W12-B the portal had no global toast: ~14 call sites each kept their
 * own `useState` message + a hand-rolled fixed `<div>` (the admin pages shared
 * `AdminToast`, everyone else rolled their own). That meant a single toast at a
 * time, no queue, inconsistent auto-dismiss, and duplicated markup.
 *
 * This provider centralises it: one `<ToastProvider>` mounted in `AppProviders`
 * renders a single stacked region, and `useToast().toast(text, opts)` pushes a
 * message from anywhere. Messages queue, auto-dismiss, and announce via an
 * `aria-live` region.
 *
 * **Test-id contract (must not change).** The e2e harnesses select toasts with
 * `[data-testid="admin-toast"][data-tone="success|error"][data-toast-key="<key>"]`
 * — and not only the admin pages: Notifications and Profile also assert against
 * `admin-toast`. So the rendered markup mirrors the old `AdminToast` exactly,
 * `testId` defaults to `"admin-toast"`, and call sites keep passing the same
 * `tone` + `key`. The one exception (ScanCancelButton) passes
 * `testId: "scan-cancel-toast"`.
 *
 * Feedback rule (design-system §Component conventions): success / non-blocking
 * notices use a toast; form-validation errors stay inline next to the field
 * (RFC 7807 `detail`), never a toast the user might miss.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { registerToastDispatcher } from "@/lib/toastBus";
import { cn } from "@/lib/utils";

export type ToastTone = "success" | "error";

export interface ToastOptions {
  /** Visual + semantic tone. Default `"success"`. */
  tone?: ToastTone;
  /**
   * Stable, locale-independent identifier surfaced as `data-toast-key` so e2e
   * tests assert on which invariant produced the toast without depending on
   * translated copy.
   */
  key?: string;
  /**
   * `data-testid` on the toast root. Defaults to `"admin-toast"` because that
   * is what nearly every existing harness selector expects; ScanCancelButton
   * overrides it with `"scan-cancel-toast"`.
   */
  testId?: string;
  /** Auto-dismiss delay. Default 4000 ms. */
  ttlMs?: number;
}

interface ToastItem {
  id: number;
  text: string;
  tone: ToastTone;
  key?: string;
  testId: string;
}

interface ToastContextValue {
  /** Push a toast. Returns nothing; it auto-dismisses after `ttlMs`. */
  toast: (text: string, options?: ToastOptions) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const DEFAULT_TTL_MS = 4000;
/** Cap the visible stack so a burst of toasts cannot bury the UI. */
const MAX_VISIBLE = 4;

/** No-op used when a component renders outside a `<ToastProvider>`. */
const NOOP_TOAST: ToastContextValue = { toast: () => undefined };

/**
 * Access the toast dispatcher. The real app always mounts `<ToastProvider>`
 * in `AppProviders`, so in production this returns the live dispatcher. Unit
 * tests that render a single component under a bare `QueryClientProvider`
 * (no full provider tree) get a safe no-op instead of a thrown error —
 * toast feedback is non-critical and the e2e suite asserts the real toasts.
 */
export function useToast(): ToastContextValue {
  return useContext(ToastContext) ?? NOOP_TOAST;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);

  const dismiss = useCallback((id: number) => {
    setItems((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toast = useCallback(
    (text: string, options?: ToastOptions) => {
      seq.current += 1;
      const id = seq.current;
      const item: ToastItem = {
        id,
        text,
        tone: options?.tone ?? "success",
        key: options?.key,
        testId: options?.testId ?? "admin-toast",
      };
      setItems((prev) => [...prev, item].slice(-MAX_VISIBLE));
      window.setTimeout(() => dismiss(id), options?.ttlMs ?? DEFAULT_TTL_MS);
    },
    [dismiss],
  );

  const value = useMemo<ToastContextValue>(() => ({ toast }), [toast]);

  // Non-React code (the global mutation error handler in lib/queryClient.ts)
  // dispatches through the toast bus; wire it to this provider's dispatcher
  // for the provider's lifetime.
  useEffect(() => {
    registerToastDispatcher(toast);
    return () => registerToastDispatcher(null);
  }, [toast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* Single stacked region, bottom-right. aria-live announces additions
          without each call site plumbing its own live region. */}
      <div
        className="pointer-events-none fixed bottom-4 right-4 z-50 flex max-w-sm flex-col gap-2"
        aria-live="polite"
        aria-atomic="false"
      >
        {items.map((t) => (
          <div
            key={t.id}
            data-testid={t.testId}
            data-tone={t.tone}
            data-toast-key={t.key ?? ""}
            className="pointer-events-auto animate-in fade-in-0 slide-in-from-bottom-2 duration-base ease-out-soft"
          >
            <Alert
              variant={t.tone === "error" ? "destructive" : "default"}
              className={cn(
                "shadow-lg",
                t.tone === "success" &&
                  "border-emerald-200 bg-emerald-50 text-emerald-900",
              )}
              role="status"
            >
              <AlertDescription>{t.text}</AlertDescription>
            </Alert>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
