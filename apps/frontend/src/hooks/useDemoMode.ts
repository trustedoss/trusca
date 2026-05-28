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
  /** True until the first `/health` response resolves (uses the build hint). */
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
  const { data, isPending } = useQuery({
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
  return { demoReadOnly, isResolving: isPending };
}
