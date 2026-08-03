/**
 * dueDateStatus — unit tests (Phase C / C3 SLA classifier).
 *
 * Boundary contract (calendar-day math, injectable clock):
 *   - due today        → imminent, days = 0 (still actionable, NOT overdue)
 *   - due in exactly 7 → imminent, days = 7 (inclusive upper bound)
 *   - due in 8         → ok, days = 8 (first day outside the window)
 *   - past due         → overdue, negative days
 *   - unparseable      → ok + NaN days (quiet degrade, never throws)
 */
import { describe, expect, it } from "vitest";

import { dueDateStatus, IMMINENT_WINDOW_DAYS } from "@/lib/dueDate";

// Local wall-clock "today" — 2026-07-03, mid-morning so the time-of-day
// component proves the classifier is calendar-day based, not 24h-interval
// based.
const NOW = new Date(2026, 6, 3, 10, 30, 0);

describe("dueDateStatus", () => {
  it("classifies a due date of today as imminent with days = 0", () => {
    expect(dueDateStatus("2026-07-03", NOW)).toEqual({
      state: "imminent",
      days: 0,
    });
  });

  it("classifies exactly +7 days as imminent (inclusive boundary)", () => {
    expect(dueDateStatus("2026-07-10", NOW)).toEqual({
      state: "imminent",
      days: IMMINENT_WINDOW_DAYS,
    });
  });

  it("classifies +8 days as ok (first day past the window)", () => {
    expect(dueDateStatus("2026-07-11", NOW)).toEqual({
      state: "ok",
      days: 8,
    });
  });

  it("classifies a past due date as overdue with negative days", () => {
    expect(dueDateStatus("2026-06-30", NOW)).toEqual({
      state: "overdue",
      days: -3,
    });
  });

  it("classifies yesterday as overdue with days = -1", () => {
    // The closest overdue boundary: one calendar day past due even though
    // fewer than 24 hours may have elapsed since local midnight.
    expect(dueDateStatus("2026-07-02", NOW)).toEqual({
      state: "overdue",
      days: -1,
    });
  });

  it("classifies a far-future date as ok", () => {
    expect(dueDateStatus("2026-12-25", NOW)).toEqual({
      state: "ok",
      days: 175,
    });
  });

  it("degrades an unparseable string to ok with NaN days", () => {
    const result = dueDateStatus("not-a-date", NOW);
    expect(result.state).toBe("ok");
    expect(Number.isNaN(result.days)).toBe(true);
  });
});
