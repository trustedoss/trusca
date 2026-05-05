import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

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

  useEffect(() => {
    function onExpired() {
      // Reset is idempotent; safe even when the interceptor already called it.
      useAuthStore.getState().reset();
      navigate("/login", { replace: true });
    }
    window.addEventListener("auth:expired", onExpired);
    return () => window.removeEventListener("auth:expired", onExpired);
  }, [navigate]);

  return null;
}
