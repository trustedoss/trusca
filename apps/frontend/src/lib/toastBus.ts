/**
 * Toast bus — lets non-React code (the TanStack Query mutation cache, WS
 * handlers) dispatch a toast through the single `<ToastProvider>` region.
 *
 * `ToastProvider` registers its dispatcher on mount and unregisters on
 * unmount; `dispatchToast` is a safe no-op until then (mirrors the
 * `useToast()` NOOP fallback for components rendered outside the provider,
 * e.g. bare unit-test trees).
 */
import type { ToastOptions } from "@/components/ui/toast";

export type ToastDispatcher = (text: string, options?: ToastOptions) => void;

let dispatcher: ToastDispatcher | null = null;

export function registerToastDispatcher(fn: ToastDispatcher | null): void {
  dispatcher = fn;
}

export function dispatchToast(text: string, options?: ToastOptions): void {
  dispatcher?.(text, options);
}
