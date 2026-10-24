// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Pins the TanStack Query behaviour the mutation guard in `createQueryClient`
 * depends on. The guard is installed by overriding `defaultMutationOptions`
 * and skips options carrying the internal `_defaulted` flag. If an upgrade
 * stops routing mutations through that method, or renames the flag, these
 * tests fail instead of the guard quietly going away.
 */
import { QueryClientProvider, useMutation } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { createQueryClient } from "@/lib/queryClient";
import { bindUserScopedState } from "@/lib/userScopedState";
import { useAuthStore, type AuthUser } from "@/stores/authStore";

const alice: AuthUser = {
  id: "alice",
  email: "alice@example.com",
  displayName: "alice",
  role: "developer",
  isActive: true,
  isSuperuser: false,
  teamId: null,
};

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

let unbind: (() => void) | null = null;

beforeEach(() => {
  useAuthStore.setState({
    user: alice,
    accessToken: "tok",
    status: "authenticated",
    isAuthenticated: true,
  });
});

afterEach(() => {
  unbind?.();
  unbind = null;
});

describe("the mutation guard's dependency on TanStack Query", () => {
  it("defaulted options carry _defaulted and pass through unchanged", () => {
    const qc = createQueryClient();
    const mutationFn = async () => "ok";

    const once = qc.defaultMutationOptions({ mutationFn });

    expect((once as { _defaulted?: boolean })._defaulted).toBe(true);
    expect(once.mutationFn).not.toBe(mutationFn);
    expect(qc.defaultMutationOptions(once)).toBe(once);
  });

  it("useMutation goes through the guard", async () => {
    const qc = createQueryClient();
    unbind = bindUserScopedState(qc);
    const pending = deferred<string>();
    const ran: string[] = [];
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(
      () =>
        useMutation({
          mutationFn: () => pending.promise,
          onSuccess: () => {
            ran.push("success");
          },
        }),
      { wrapper },
    );

    act(() => {
      result.current.mutate();
    });
    await flush();
    act(() => {
      useAuthStore.getState().reset();
    });
    pending.resolve("late");
    await flush();

    expect(ran).toEqual([]);
  });

  it("a mutation built straight from the cache goes through the guard", async () => {
    const qc = createQueryClient();
    unbind = bindUserScopedState(qc);
    const pending = deferred<string>();
    const ran: string[] = [];
    const mutation = qc.getMutationCache().build(qc, {
      mutationFn: () => pending.promise,
      onSuccess: () => {
        ran.push("success");
      },
    });

    void mutation.execute(undefined).catch(() => undefined);
    await flush();
    act(() => {
      useAuthStore.getState().reset();
    });
    pending.resolve("late");
    await flush();

    expect(ran).toEqual([]);
  });
});
