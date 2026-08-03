/**
 * queryClient.ts — global mutation error toast unit tests.
 *
 * The MutationCache-level `onError` is the safety net that keeps a failed
 * write from staying silent (the W-audit found mutations with no onError, no
 * catch, and no inline rendering). These tests pin the dispatch contract:
 *
 *   - a mutation WITHOUT its own onError → one error toast (ProblemError
 *     detail preferred, generic i18n fallback otherwise);
 *   - a mutation WITH its own onError → global toast stays quiet;
 *   - `meta.errorToast: false` → quiet even without a local handler
 *     (inline-rendered errors);
 *   - `meta.errorToast: true` → forced even with a local handler
 *     (rollback-only handlers);
 *   - 422 validation problems → quiet (design-system rule: validation is
 *     inline, never a toast).
 *
 * `onMutationError` is exercised through a REAL QueryClient + mutation
 * execution (not by calling the helper directly) so the wiring through
 * `new MutationCache({ onError })` is covered too.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createQueryClient } from "@/lib/queryClient";
import { ProblemError } from "@/lib/problem";
import { registerToastDispatcher, type ToastDispatcher } from "@/lib/toastBus";

function problem(status: number, detail: string): ProblemError {
  return new ProblemError(detail, {
    status,
    title: `HTTP ${status}`,
    detail,
    problem: {
      type: "about:blank",
      title: `HTTP ${status}`,
      status,
      detail,
    },
  });
}

async function runFailingMutation(
  client: ReturnType<typeof createQueryClient>,
  options: {
    error: unknown;
    onError?: () => void;
    meta?: { errorToast?: boolean };
  },
): Promise<void> {
  const mutation = client.getMutationCache().build(client, {
    mutationFn: () => Promise.reject(options.error),
    onError: options.onError,
    meta: options.meta,
  });
  await expect(mutation.execute(undefined)).rejects.toBe(options.error);
}

describe("global mutation error toast", () => {
  let dispatched: Array<{ text: string; options?: Parameters<ToastDispatcher>[1] }>;
  let client: ReturnType<typeof createQueryClient>;

  beforeEach(() => {
    dispatched = [];
    registerToastDispatcher((text, options) => {
      dispatched.push({ text, options });
    });
    client = createQueryClient();
  });

  afterEach(() => {
    registerToastDispatcher(null);
    client.clear();
    vi.restoreAllMocks();
  });

  it("toasts the ProblemError detail for a mutation without a local onError", async () => {
    await runFailingMutation(client, { error: problem(409, "Scan already queued.") });
    expect(dispatched).toHaveLength(1);
    expect(dispatched[0].text).toBe("Scan already queued.");
    expect(dispatched[0].options).toMatchObject({
      tone: "error",
      key: "mutation-error",
    });
  });

  it("falls back to the generic i18n message for non-Problem errors", async () => {
    await runFailingMutation(client, { error: new TypeError("Failed to fetch") });
    expect(dispatched).toHaveLength(1);
    // The i18n default language in tests is EN.
    expect(dispatched[0].text).toBe("The request failed. Please try again.");
  });

  it("stays quiet when the mutation defines its own onError", async () => {
    const local = vi.fn();
    await runFailingMutation(client, {
      error: problem(500, "boom"),
      onError: local,
    });
    expect(local).toHaveBeenCalledTimes(1);
    expect(dispatched).toHaveLength(0);
  });

  it("stays quiet when meta.errorToast is false (inline-rendered errors)", async () => {
    await runFailingMutation(client, {
      error: problem(409, "Waive conflict."),
      meta: { errorToast: false },
    });
    expect(dispatched).toHaveLength(0);
  });

  it("fires when meta.errorToast is true even with a local onError (rollback-only handlers)", async () => {
    const local = vi.fn();
    await runFailingMutation(client, {
      error: problem(500, "Status update failed."),
      onError: local,
      meta: { errorToast: true },
    });
    expect(local).toHaveBeenCalledTimes(1);
    expect(dispatched).toHaveLength(1);
    expect(dispatched[0].text).toBe("Status update failed.");
  });

  it("stays quiet for 422 validation problems (inline per design system)", async () => {
    await runFailingMutation(client, {
      error: problem(422, "name: field required"),
    });
    expect(dispatched).toHaveLength(0);
  });

  it("forces the toast for 422 when meta.errorToast is true", async () => {
    await runFailingMutation(client, {
      error: problem(422, "name: field required"),
      meta: { errorToast: true },
    });
    expect(dispatched).toHaveLength(1);
  });

  it("is a safe no-op when no dispatcher is registered (bare test trees)", async () => {
    registerToastDispatcher(null);
    await runFailingMutation(client, { error: problem(500, "boom") });
    expect(dispatched).toHaveLength(0);
  });
});
