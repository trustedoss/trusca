// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * useDemoMode — v2.1 Track B (B5) live read-only demo detection.
 *
 * The public live demo runs the SAME image as a normal deploy, with the backend
 * env flag `DEMO_READ_ONLY` flipped on. So the SPA cannot decide "am I a demo"
 * purely from its own build — it asks the backend, which surfaces the runtime
 * flag on the PUBLIC `GET /health` probe (`{ status, demo_read_only }`).
 *
 * Resolution order:
 *   1. The runtime backend flag from `/health` (authoritative). This is the
 *      value the read-only MIDDLEWARE actually enforces, so the UI never claims
 *      "you can write" when the backend would 403, and vice-versa.
 *   2. A build-time hint `VITE_DEMO_READ_ONLY` (optional). Used only as the
 *      *initial* value so the banner can paint on first frame before the
 *      `/health` round-trip resolves; the backend value always wins once known.
 *
 * `import.meta.env` is read inside the function (CLAUDE.md rule #11 spirit), not
 * cached at module scope.
 */
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

interface HealthResponse {
  status: string;
  demo_read_only?: boolean;
  /**
   * feat/demo-sandbox-scan — when the read-only demo has the sandbox carve-out
   * enabled, the backend allows TWO writes (source scan trigger + SBOM ingest)
   * against the seeded "Demo Sandbox" project only. Every other write still
   * 403s. `true` ONLY when `demo_read_only` is also true (the backend couples
   * them), so the UI treats it as "read-only demo, plus a sandbox lane".
   */
  demo_sandbox_scans?: boolean;
}

/** Build-time hint; only seeds the initial render. Backend value is canonical. */
function buildTimeDemoHint(): boolean {
  const raw = import.meta.env.VITE_DEMO_READ_ONLY as string | boolean | undefined;
  if (typeof raw === "boolean") return raw;
  if (typeof raw !== "string") return false;
  return ["1", "true", "yes", "on"].includes(raw.trim().toLowerCase());
}

export async function fetchHealth(): Promise<HealthResponse> {
  const { data } = await api.get<HealthResponse>("/health");
  return data;
}

export interface DemoModeState {
  /** True when the backend is enforcing the read-only demo guard. */
  demoReadOnly: boolean;
  /**
   * True when the read-only demo also enables the sandbox carve-out: source
   * scans + SBOM ingest are permitted against the seeded "Demo Sandbox"
   * project (see {@link isDemoSandboxProjectName}). Never seeded from the
   * build hint — only the authoritative `/health` value flips it on, so
   * sandbox affordances never flash before the backend confirms them.
   */
  demoSandboxScans: boolean;
  /**
   * True until the first `/health` response resolves, while `demoReadOnly`
   * still carries the build hint rather than the backend's answer.
   *
   * A surface that only ADDS chrome when the demo is confirmed (the banner, the
   * credentials hint) can ignore this: showing nothing for one frame is
   * invisible. A surface that REMOVES something instead (the register form,
   * which the demo replaces with a notice) must gate on it, or the visitor sees
   * the form paint and then vanish.
   */
  isResolving: boolean;
}

/**
 * Returns whether the portal is running as a read-only live demo. Cheap to call
 * from multiple components — TanStack Query dedupes the `/health` fetch and the
 * result is cached for the whole session (the flag does not change at runtime
 * within a single deploy).
 */
export function useDemoMode(): DemoModeState {
  const hint = buildTimeDemoHint();
  const { data, isPending, isPlaceholderData } = useQuery({
    queryKey: ["health", "demo-mode"],
    queryFn: fetchHealth,
    // The deploy-level flag is stable for the life of the page; no need to poll.
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
    // Seed with the build hint so the banner can render immediately; refetch
    // replaces it with the authoritative backend value.
    placeholderData: { status: "ok", demo_read_only: hint },
  });

  const demoReadOnly = data?.demo_read_only ?? hint;
  // Gate on demoReadOnly defensively: the backend only reports the sandbox flag
  // alongside read-only, but we never want to re-enable a write when the UI
  // thinks writes are open (a non-demo deploy).
  const demoSandboxScans = demoReadOnly && (data?.demo_sandbox_scans ?? false);
  // `placeholderData` makes the query report success on the first render, so
  // `isPending` alone is false from the very first frame and would claim the
  // flag is settled while it is still the build hint. `isPlaceholderData` is
  // what actually distinguishes "seeded guess" from "backend answered".
  return {
    demoReadOnly,
    demoSandboxScans,
    isResolving: isPending || isPlaceholderData,
  };
}
