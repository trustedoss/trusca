// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
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
 * message from anywhere. Messages queue and announce via an `aria-live`
 * region. Success toasts auto-dismiss; error toasts stay until the reader
 * closes them, because an error that vanishes after four seconds is easy to
 * miss and its text is often what the reader needs to act on. Every toast
 * has a close button, and error toasts use role="alert" so a screen reader
 * announces them at once rather than when it is next idle. Errors sit in
 * their own container outside the polite live region: an alert inside a
 * live region is announced twice by some screen readers. Persistent errors
 * are cleared when the signed-in user changes (logout, session expiry), so
 * the next person at the screen does not inherit them.
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
import { X } from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FocusEvent,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { registerToastDispatcher } from "@/lib/toastBus";
import { useAuthStore } from "@/stores/authStore";
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
  /**
   * Auto-dismiss delay. Defaults to 4000 ms for success toasts; error toasts
   * stay until closed unless a delay is passed explicitly.
   */
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
  /** Push a toast. Success toasts auto-dismiss after `ttlMs`; errors stay. */
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
  const { t: translate } = useTranslation("common");
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);

  const regionRef = useRef<HTMLDivElement>(null);
  // Where focus was before it entered the toast stack, so closing the last
  // toast can hand it back instead of dropping it on <body>.
  const returnFocusRef = useRef<HTMLElement | null>(null);

  const dismiss = useCallback((id: number) => {
    setItems((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const onRegionFocus = useCallback((event: FocusEvent<HTMLDivElement>) => {
    const from = event.relatedTarget as HTMLElement | null;
    // <body> is where focus sits when nothing has it; handing focus back
    // there is the outcome this exists to avoid.
    if (
      from &&
      from !== document.body &&
      !regionRef.current?.contains(from)
    ) {
      returnFocusRef.current = from;
    }
  }, []);

  const closeFromButton = useCallback(
    (id: number) => {
      const region = regionRef.current;
      const hadFocus =
        region !== null && region.contains(document.activeElement);
      if (hadFocus && region) {
        // The button about to disappear has focus. Move it to the next close
        // button in the stack, else back to where it came from, else to the
        // main content area (AppShell's skip target).
        const buttons = Array.from(
          region.querySelectorAll<HTMLButtonElement>("[data-toast-dismiss-id]"),
        );
        const index = buttons.findIndex(
          (b) => b.dataset.toastDismissId === String(id),
        );
        const next = buttons[index + 1] ?? buttons[index - 1] ?? null;
        if (next) {
          next.focus();
        } else {
          const back = returnFocusRef.current;
          returnFocusRef.current = null;
          if (back && back.isConnected) back.focus();
          else document.getElementById("main-content")?.focus();
        }
      }
      dismiss(id);
    },
    [dismiss],
  );

  // Clear persistent errors when the signed-in user goes away or changes.
  useEffect(
    () =>
      useAuthStore.subscribe((state, prev) => {
        const before = prev.user?.id ?? null;
        const after = state.user?.id ?? null;
        if (before !== null && before !== after) {
          setItems((current) => current.filter((t) => t.tone !== "error"));
        }
      }),
    [],
  );

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
      setItems((prev) => {
        // A persistent error repeated under the same key (a retry that fails
        // the same way) replaces the earlier one instead of stacking copies.
        const kept =
          item.tone === "error" && item.key
            ? prev.filter((p) => !(p.tone === "error" && p.key === item.key))
            : prev;
        return [...kept, item].slice(-MAX_VISIBLE);
      });
      const ttl =
        options?.ttlMs ?? (item.tone === "error" ? null : DEFAULT_TTL_MS);
      if (ttl !== null) {
        window.setTimeout(() => dismiss(id), ttl);
      }
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

  const renderToast = (t: ToastItem) => (
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
          "pr-10 shadow-lg",
          t.tone === "success" &&
            "border-emerald-200 bg-emerald-50 text-emerald-900",
        )}
        role={t.tone === "error" ? "alert" : "status"}
      >
        <AlertDescription>{t.text}</AlertDescription>
        <button
          type="button"
          onClick={() => closeFromButton(t.id)}
          aria-label={translate("toast.dismiss")}
          data-testid="toast-dismiss"
          data-toast-dismiss-id={t.id}
          className="absolute right-2 top-2 rounded-sm p-1 opacity-70 transition-opacity duration-fast ease-out-soft hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      </Alert>
    </div>
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* One stacked area, bottom-right. Success toasts go in the polite
          live region; errors sit outside it and announce through their own
          role="alert", so no screen reader reads them twice. */}
      <div
        ref={regionRef}
        onFocus={onRegionFocus}
        className="pointer-events-none fixed bottom-4 right-4 z-50 flex max-w-sm flex-col gap-2"
        data-testid="toast-region"
      >
        <div className="flex flex-col gap-2" data-testid="toast-errors">
          {items.filter((t) => t.tone === "error").map(renderToast)}
        </div>
        <div
          className="flex flex-col gap-2"
          aria-live="polite"
          aria-atomic="false"
          data-testid="toast-status"
        >
          {items.filter((t) => t.tone !== "error").map(renderToast)}
        </div>
      </div>
    </ToastContext.Provider>
  );
}
