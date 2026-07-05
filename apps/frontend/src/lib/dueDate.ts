/**
 * Due-date SLA classifier — Phase C / C3 (KEV remediation deadline).
 *
 * CISA KEV catalog entries carry a date-only remediation deadline
 * (`kev_due_date`, e.g. "2026-07-15"). The FE surfaces three urgency states:
 *
 *   - "overdue"  — the deadline has passed (calendar day strictly before
 *                  today). `days` is negative: -1 means "one day past due".
 *   - "imminent" — the deadline is today or within the next 7 calendar days
 *                  (0 <= days <= 7).
 *   - "ok"       — more than 7 calendar days remain (days >= 8).
 *
 * The comparison is calendar-day based, not millisecond based: a due date of
 * "today" is imminent (still actionable), not overdue. The due date string is
 * parsed as an ISO date (UTC midnight per ECMA-262 for date-only forms) and
 * compared against the *local* calendar date of `now`, because the operator
 * thinks in their own "today".
 *
 * Pure function — `now` is injectable so unit tests and render surfaces
 * (KevBadge) stay deterministic.
 *
 * Garbage-in: an unparseable `iso` returns `{ state: "ok", days: NaN }` so a
 * malformed feed value degrades to the quiet default instead of throwing
 * mid-render. Callers that must distinguish can check
 * `Number.isFinite(result.days)`.
 */

export type DueDateState = "overdue" | "imminent" | "ok";

export interface DueDateStatus {
  state: DueDateState;
  /** Calendar days from today to the due date. Negative = overdue. */
  days: number;
}

const DAY_MS = 24 * 60 * 60 * 1000;

/** Days within which a not-yet-due deadline is flagged "imminent". */
export const IMMINENT_WINDOW_DAYS = 7;

export function dueDateStatus(iso: string, now: Date = new Date()): DueDateStatus {
  const due = new Date(iso);
  if (Number.isNaN(due.getTime())) {
    return { state: "ok", days: Number.NaN };
  }

  // Date-only ISO strings parse as UTC midnight, so read the due date's
  // calendar parts in UTC; `now` is a wall-clock instant, so read its
  // calendar parts in local time (the operator's "today").
  const dueDay = Date.UTC(due.getUTCFullYear(), due.getUTCMonth(), due.getUTCDate());
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  const days = Math.round((dueDay - today) / DAY_MS);

  if (days < 0) return { state: "overdue", days };
  if (days <= IMMINENT_WINDOW_DAYS) return { state: "imminent", days };
  return { state: "ok", days };
}
