// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { useAuthStore } from "@/stores/authStore";

/**
 * Listens for the `auth:expired` window event dispatched by the axios
 * response interceptor when /auth/refresh fails. Resets the store (defensive
 * — the interceptor already calls reset()) and bounces the user to /login.
 *
 * Lives inside the router tree so `useNavigate()` is available; keeping the
 * navigation here (not in the store) preserves the store's router-free
 * invariant (CLAUDE.md "store에 router import 하지 마라").
 */
export function AuthExpiredListener() {
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    function onExpired() {
      // Reset is idempotent; safe even when the interceptor already called it.
      useAuthStore.getState().reset();
      // A5: carry two things to the sign-in screen. Where the user was, so
      // signing in again puts them back rather than on the dashboard; and
      // the fact that this was an expiry, so the screen can say why it is
      // asking. Without either, a session timing out looked identical to
      // arriving at /login on purpose, and the page the user had open was
      // simply gone.
      //
      // Router state, not a query parameter: a link is something an
      // attacker can hand someone, and "your session expired" is a sentence
      // worth being unable to forge. It is lost on reload, which costs
      // nothing, because after a reload the user is only looking at /login.
      navigate("/login", {
        replace: true,
        state: {
          from: location.pathname + location.search + location.hash,
          expired: true,
        },
      });
    }
    window.addEventListener("auth:expired", onExpired);
    return () => window.removeEventListener("auth:expired", onExpired);
  }, [navigate, location]);

  return null;
}
