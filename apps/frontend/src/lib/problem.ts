/**
 * RFC 7807 Problem Details — shared error type used by every HTTP layer.
 *
 * Originally lived in `lib/authApi.ts` (PR #6 / 1.6, fetch-based). Promoted
 * out so the axios surface in `lib/api.ts` (PR #6 / 1.7) can throw the same
 * shape and the auth pages don't have to learn a second error class.
 *
 * Contract:
 *   - `status === 0` is reserved for transport-level failures (network, CORS,
 *     DNS, aborted). The backend never returns 0.
 *   - `detail` is always populated (falls back to `title`) so the UI can
 *     render `err.detail` without further branching.
 *   - `problem` is the parsed JSON when the server returned `application/
 *     problem+json`; `null` when the body wasn't JSON or the request never
 *     reached the server.
 */

export interface ProblemDetails {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance?: string;
}

export class ProblemError extends Error {
  readonly status: number;
  readonly title: string;
  readonly detail: string;
  readonly problem: ProblemDetails | null;

  constructor(
    message: string,
    options: {
      status: number;
      title: string;
      detail: string;
      problem: ProblemDetails | null;
    },
  ) {
    super(message);
    this.name = "ProblemError";
    this.status = options.status;
    this.title = options.title;
    this.detail = options.detail;
    this.problem = options.problem;
  }
}

/**
 * Parse an arbitrary JSON-ish body into a {@link ProblemDetails} when the
 * shape matches RFC 7807. Returns null if the body isn't an object.
 *
 * Used by both the fetch-based legacy path (`lib/authApi.ts`) and the axios
 * response interceptor (`lib/api.ts`).
 */
export function parseProblemBody(
  data: unknown,
  fallback: { status: number; statusText?: string },
): { problem: ProblemDetails | null; title: string; detail: string } {
  let title = fallback.statusText || `HTTP ${fallback.status}`;
  let detail = "";
  let problem: ProblemDetails | null = null;
  if (data && typeof data === "object") {
    const obj = data as Record<string, unknown>;
    if (typeof obj.title === "string") title = obj.title;
    if (typeof obj.detail === "string") detail = obj.detail;
    problem = {
      type: typeof obj.type === "string" ? obj.type : "about:blank",
      title,
      status: typeof obj.status === "number" ? obj.status : fallback.status,
      detail: detail || title,
      instance: typeof obj.instance === "string" ? obj.instance : undefined,
    };
  }
  return { problem, title, detail: detail || title };
}
