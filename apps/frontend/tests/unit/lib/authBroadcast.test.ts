// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Sign-in and sign-out reach the other tabs of the browser.
 *
 * "The other tab" is a second BroadcastChannel on the same name, which is how
 * a real second tab looks to this one. The fallback path is driven with the
 * `storage` events a real second tab would cause.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  AUTH_CHANNEL_NAME,
  AUTH_STORAGE_KEY,
  bindAuthBroadcast,
  type AuthMessage,
} from "@/lib/authBroadcast";
import { useAuthStore, type AuthUser } from "@/stores/authStore";

function user(id: string): AuthUser {
  return {
    id,
    email: `${id}@example.com`,
    displayName: id,
    role: "developer",
    isActive: true,
    isSuperuser: false,
    teamId: null,
  };
}

function signIn(u: AuthUser) {
  useAuthStore.setState({
    user: u,
    accessToken: `tok-${u.id}`,
    status: "authenticated",
    isAuthenticated: true,
  });
}

let unbind: (() => void) | null = null;
let otherTab: BroadcastChannel | null = null;

beforeEach(() => {
  useAuthStore.setState({
    user: null,
    accessToken: null,
    status: "anonymous",
    isAuthenticated: false,
  });
});

afterEach(() => {
  unbind?.();
  unbind = null;
  otherTab?.close();
  otherTab = null;
  vi.unstubAllGlobals();
});

function listenAsOtherTab(): AuthMessage[] {
  const received: AuthMessage[] = [];
  otherTab = new BroadcastChannel(AUTH_CHANNEL_NAME);
  otherTab.onmessage = (event: MessageEvent) => {
    received.push(event.data as AuthMessage);
  };
  return received;
}

/** Let any message posted so far be delivered. */
async function settle() {
  await new Promise((r) => setTimeout(r, 30));
}

describe("over BroadcastChannel", () => {
  it("tells other tabs when this tab signs out or signs in", async () => {
    signIn(user("alice"));
    unbind = bindAuthBroadcast();
    const received = listenAsOtherTab();

    useAuthStore.getState().reset();
    signIn(user("bob"));

    await vi.waitFor(() => expect(received).toHaveLength(2));
    expect(received).toEqual([
      { type: "signed-out" },
      { type: "signed-in", userId: "bob" },
    ]);
  });

  it("signs this tab out when another tab signs out, without echoing it", async () => {
    signIn(user("alice"));
    unbind = bindAuthBroadcast();
    const received = listenAsOtherTab();

    otherTab!.postMessage({ type: "signed-out" });

    await vi.waitFor(() => expect(useAuthStore.getState().user).toBeNull());
    expect(useAuthStore.getState().accessToken).toBeNull();
    await settle();
    expect(received).toEqual([]);
  });

  it("signs this tab out when another tab signs in as someone else", async () => {
    signIn(user("alice"));
    unbind = bindAuthBroadcast();
    listenAsOtherTab();

    otherTab!.postMessage({ type: "signed-in", userId: "bob" });

    await vi.waitFor(() => expect(useAuthStore.getState().user).toBeNull());
  });

  it("stays signed in when another tab signs in as the same user", async () => {
    signIn(user("alice"));
    unbind = bindAuthBroadcast();
    listenAsOtherTab();

    otherTab!.postMessage({ type: "signed-in", userId: "alice" });
    otherTab!.postMessage({ type: "something-else" });
    await settle();

    expect(useAuthStore.getState().user?.id).toBe("alice");
  });

  it("stops listening once unbound", async () => {
    signIn(user("alice"));
    unbind = bindAuthBroadcast();
    unbind();
    unbind = null;
    listenAsOtherTab();

    otherTab!.postMessage({ type: "signed-out" });
    await settle();

    expect(useAuthStore.getState().user?.id).toBe("alice");
  });
});

describe("over storage events, where BroadcastChannel is missing", () => {
  function storageEvent(value: string | null, key = AUTH_STORAGE_KEY) {
    window.dispatchEvent(new StorageEvent("storage", { key, newValue: value }));
  }

  beforeEach(() => {
    vi.stubGlobal("BroadcastChannel", undefined);
  });

  it("writes a fresh entry for every change", () => {
    signIn(user("alice"));
    unbind = bindAuthBroadcast();

    useAuthStore.getState().reset();
    const first = window.localStorage.getItem(AUTH_STORAGE_KEY);
    signIn(user("bob"));
    const second = window.localStorage.getItem(AUTH_STORAGE_KEY);

    expect(JSON.parse(first as string)).toMatchObject({ type: "signed-out" });
    expect(JSON.parse(second as string)).toMatchObject({
      type: "signed-in",
      userId: "bob",
    });
  });

  it("signs this tab out when another tab signs out", () => {
    signIn(user("alice"));
    unbind = bindAuthBroadcast();

    storageEvent(JSON.stringify({ type: "signed-out", nonce: "1" }));

    expect(useAuthStore.getState().user).toBeNull();
  });

  it("ignores other keys, removals and unreadable values", () => {
    signIn(user("alice"));
    unbind = bindAuthBroadcast();

    storageEvent(JSON.stringify({ type: "signed-out" }), "some-other-key");
    storageEvent(null);
    storageEvent("{not json");

    expect(useAuthStore.getState().user?.id).toBe("alice");
  });

  it("stops listening once unbound", () => {
    signIn(user("alice"));
    unbind = bindAuthBroadcast();
    unbind();
    unbind = null;

    storageEvent(JSON.stringify({ type: "signed-out" }));

    expect(useAuthStore.getState().user?.id).toBe("alice");
  });
});
