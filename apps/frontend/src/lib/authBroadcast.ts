// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useAuthStore } from "@/stores/authStore";

/**
 * Tell the other tabs of this browser when the signed-in user changes.
 *
 * Every tab shares one refresh cookie, so a sign-out or a sign-in in one tab
 * changes the session for all of them, but each tab keeps its own user,
 * token and query cache in memory. Without a message, a second tab went on
 * showing the previous user's screens until its next refresh.
 *
 * A tab that hears another tab signed out, or signed in as a different user,
 * resets its own auth state. That drops its cache through
 * `bindUserScopedState`, and `RequireAuth` sends it to the sign-in screen.
 *
 * The message goes over a BroadcastChannel where there is one, and over a
 * localStorage `storage` event otherwise. Both deliver only to other tabs.
 */

export const AUTH_CHANNEL_NAME = "trusca-auth";
export const AUTH_STORAGE_KEY = "trusca-auth-event";

export type AuthMessage =
  | { type: "signed-out" }
  | { type: "signed-in"; userId: string };

function parse(value: unknown): AuthMessage | null {
  if (typeof value !== "object" || value === null) return null;
  const msg = value as { type?: unknown; userId?: unknown };
  if (msg.type === "signed-out") return { type: "signed-out" };
  if (msg.type === "signed-in" && typeof msg.userId === "string") {
    return { type: "signed-in", userId: msg.userId };
  }
  return null;
}

/** Returns the unsubscribe function. */
export function bindAuthBroadcast(): () => void {
  // Set while applying a message from another tab, so the reset it causes
  // is not sent back out as this tab's own sign-out.
  let applyingRemote = false;

  function receive(msg: AuthMessage | null): void {
    if (msg === null) return;
    const local = useAuthStore.getState().user;
    if (local === null) return;
    if (msg.type === "signed-in" && msg.userId === local.id) return;
    applyingRemote = true;
    try {
      useAuthStore.getState().reset();
    } finally {
      applyingRemote = false;
    }
  }

  let send: (msg: AuthMessage) => void;
  let close: () => void;

  if (typeof BroadcastChannel !== "undefined") {
    const channel = new BroadcastChannel(AUTH_CHANNEL_NAME);
    channel.onmessage = (event: MessageEvent) => receive(parse(event.data));
    send = (msg) => channel.postMessage(msg);
    close = () => channel.close();
  } else {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== AUTH_STORAGE_KEY || event.newValue === null) return;
      try {
        receive(parse(JSON.parse(event.newValue)));
      } catch {
        // Not ours to interpret.
      }
    };
    window.addEventListener("storage", onStorage);
    send = (msg) => {
      try {
        // The nonce makes every write a change, so a repeat of the same
        // message still fires the event in the other tabs.
        window.localStorage.setItem(
          AUTH_STORAGE_KEY,
          JSON.stringify({ ...msg, nonce: `${Date.now()}-${Math.random()}` }),
        );
      } catch {
        // Storage can be unavailable (private mode, quota). The other tabs
        // then find out at their next refresh, as before.
      }
    };
    close = () => window.removeEventListener("storage", onStorage);
  }

  const unsubscribe = useAuthStore.subscribe((state, prev) => {
    const previousId = prev.user?.id ?? null;
    const nextId = state.user?.id ?? null;
    if (previousId === nextId || applyingRemote) return;
    send(
      nextId === null
        ? { type: "signed-out" }
        : { type: "signed-in", userId: nextId },
    );
  });

  return () => {
    unsubscribe();
    close();
  };
}
