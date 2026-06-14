/**
 * FE ↔ BE catalog-mirror contract tests — PR-6 FE regression guards.
 *
 * WHY: 13 of the 70 verified defects were FE-exposed, and the H-5 class is
 * the nastiest of them — a vocabulary lives twice (Postgres enum / backend
 * catalog on one side, an FE mirror constant + label map on the other) and
 * each side's own tests stay green while they silently drift apart. The
 * drift only surfaces when a real row of the new kind reaches the browser:
 * fallback icon, raw i18n key, a filter that can't select an emitted value.
 * The backend half of this guard is `apps/backend/tests/unit/
 * test_catalog_contracts.py` (PR #373); this file is the frontend half.
 *
 * First run of these assertions surfaced three live drifts, fixed in the
 * same PR:
 *   - `NotificationKind` was missing `approval_state_changed` (BE migration
 *     0030 / H-5) → fallback icon + raw `kind.approval_state_changed` key.
 *   - `KNOWN_OBLIGATION_KINDS` was missing `patent` (BE H-9 fix) → the kind
 *     filter and ranked chips treated an emitted obligation as "unknown".
 *   - `ProjectStatusBadge` rendered `cancelled` with the `status.failed`
 *     label although `status.cancelled` existed in both locales.
 *
 * Scope note — vulnerability 7-state: the VOCABULARY + transition matrix is
 * already pinned against the backend by
 * `tests/unit/features/projects/vulnerabilityTransitions.test.ts`; here we
 * only add the label-map half (every state owns an EN + KO label).
 */
import { describe, expect, it } from "vitest";

import { NOTIFICATION_KINDS } from "@/features/notifications/api/notificationsApi";
import { KNOWN_OBLIGATION_KINDS } from "@/features/projects/api/obligationsApi";
import { ALL_VULNERABILITY_STATUSES } from "@/features/projects/lib/vulnerabilityTransitions";
import { visualFor } from "@/features/projects/components/ProjectStatusBadge";
import {
  SBOM_CHECK_IDS,
  SCAN_KIND_VALUES,
  SCAN_STATUS_VALUES,
} from "@/lib/projectsApi";

import enAdmin from "@/locales/en/admin.json";
import koAdmin from "@/locales/ko/admin.json";
import enNotifications from "@/locales/en/notifications.json";
import koNotifications from "@/locales/ko/notifications.json";
import enProjectDetail from "@/locales/en/project_detail.json";
import koProjectDetail from "@/locales/ko/project_detail.json";
import enProjects from "@/locales/en/projects.json";
import koProjects from "@/locales/ko/projects.json";
import enScans from "@/locales/en/scans.json";
import koScans from "@/locales/ko/scans.json";

// Shared cross-app fixture (repo root). The backend enum and this FE mirror
// must both equal this list; asserting the BE side against the same file is
// the tracked follow-up (see the fixture's $comment).
import notificationKindsFixture from "../../../../../tests/contracts/notification-kinds.json";

function labelMap(ns: unknown, ...path: string[]): Record<string, string> {
  let node: unknown = ns;
  for (const key of path) {
    node = (node as Record<string, unknown>)[key];
  }
  return node as Record<string, string>;
}

describe("notification kinds — FE mirror of the notification_kind enum", () => {
  // H-5 latent-drift guard: the enum gained `approval_state_changed`
  // (migration 0030) while the FE union silently stayed at six values.
  it("NOTIFICATION_KINDS equals the shared fixture, in enum order", () => {
    expect([...NOTIFICATION_KINDS]).toEqual(notificationKindsFixture.kinds);
  });

  // The icon / tone maps in NotificationsPage are `Record<NotificationKind,…>`
  // so the type checker forces an entry per kind once the union above is
  // correct — no runtime assertion needed for them. Labels are plain JSON,
  // so they DO need the runtime walk:
  it.each([
    ["en", enNotifications],
    ["ko", koNotifications],
  ])("every kind owns a %s `kind.*` label", (_locale, ns) => {
    const kinds = labelMap(ns, "kind");
    for (const kind of notificationKindsFixture.kinds) {
      expect(kinds[kind], `kind.${kind} missing`).toBeTruthy();
    }
  });
});

describe("obligation kinds — FE mirror of KNOWN_OBLIGATION_KINDS", () => {
  // H-9 latent-drift guard: the backend advertises the catalog's emitted
  // vocabulary in `schemas/obligation_detail.py::KNOWN_OBLIGATION_KINDS`;
  // the FE copy drives the kind filter + ranked chip ordering. The column
  // itself is open (unknown kinds render verbatim), so a missing value here
  // never crashes — it just silently degrades, which is exactly why only a
  // contract test catches it.
  it("matches the backend's advertised vocabulary, in canonical order", () => {
    expect([...KNOWN_OBLIGATION_KINDS]).toEqual([
      "attribution",
      "notice",
      "source-disclosure",
      "copyleft",
      "modifications",
      "dynamic-linking",
      "no-endorsement",
      "patent",
    ]);
  });

  it.each([
    ["en", enProjectDetail],
    ["ko", koProjectDetail],
  ])("every known kind owns a %s `obligations.kind.*` label", (_locale, ns) => {
    const kinds = labelMap(ns, "obligations", "kind");
    for (const kind of KNOWN_OBLIGATION_KINDS) {
      expect(kinds[kind], `obligations.kind.${kind} missing`).toBeTruthy();
    }
  });
});

describe("scan statuses — FE mirror of SCAN_STATUS_VALUES", () => {
  it("matches the backend's closed status set, in enum order", () => {
    // Sourced from `apps/backend/models/scan.py::SCAN_STATUS_VALUES`.
    expect([...SCAN_STATUS_VALUES]).toEqual([
      "queued",
      "running",
      "succeeded",
      "failed",
      "cancelled",
    ]);
  });

  it.each([
    ["en", enProjects],
    ["ko", koProjects],
  ])("every status owns a %s `status.*` label", (_locale, ns) => {
    const statuses = labelMap(ns, "status");
    for (const status of SCAN_STATUS_VALUES) {
      expect(statuses[status], `status.${status} missing`).toBeTruthy();
    }
    // The badge's never-scanned pseudo-state needs a label too.
    expect(statuses.idle).toBeTruthy();
  });

  it("ProjectStatusBadge maps every status to its OWN status.* key", () => {
    // Pins the i18nKey pairing — `cancelled` borrowed `status.failed` for
    // two releases and nothing failed because color/testid were correct and
    // the word "Failed" is a plausible string for a terminated scan.
    for (const status of SCAN_STATUS_VALUES) {
      expect(visualFor(status).i18nKey).toBe(`status.${status}`);
    }
    expect(visualFor(null).i18nKey).toBe("status.idle");
    expect(visualFor("idle").i18nKey).toBe("status.idle");
  });
});

describe("scan kinds — FE mirror of the scan `kind` set", () => {
  // Same latent-drift class as scan statuses: `kind` was a bare string union
  // until the external SBOM ingest (PR #406) added a third emitted value. Each
  // emitted/selectable kind renders through a dynamic `…kind.${scan.kind}` key
  // and the admin filter offers `KIND_OPTIONS`, so a missing label silently
  // shows a raw i18n key (table badge) or an un-selectable raw value (filter).
  it("matches the backend's closed kind set, in canonical order", () => {
    expect([...SCAN_KIND_VALUES]).toEqual(["source", "container", "sbom"]);
  });

  it.each([
    ["en", enScans],
    ["ko", koScans],
  ])("every kind owns a %s ScansPage `page.kind.*` label", (_locale, ns) => {
    const kinds = labelMap(ns, "page", "kind");
    for (const kind of SCAN_KIND_VALUES) {
      expect(kinds[kind], `page.kind.${kind} missing`).toBeTruthy();
    }
  });

  it.each([
    ["en", enProjectDetail],
    ["ko", koProjectDetail],
  ])(
    "every kind owns a %s `overview.recent_scans.kind.*` label",
    (_locale, ns) => {
      const kinds = labelMap(ns, "overview", "recent_scans", "kind");
      for (const kind of SCAN_KIND_VALUES) {
        expect(
          kinds[kind],
          `overview.recent_scans.kind.${kind} missing`,
        ).toBeTruthy();
      }
    },
  );

  it.each([
    ["en", enAdmin],
    ["ko", koAdmin],
  ])(
    "every kind owns a %s admin `scans.filter.kind.*` label",
    (_locale, ns) => {
      const kinds = labelMap(ns, "admin", "scans", "filter", "kind");
      for (const kind of SCAN_KIND_VALUES) {
        expect(
          kinds[kind],
          `admin scans.filter.kind.${kind} missing`,
        ).toBeTruthy();
      }
    },
  );
});

describe("SBOM conformance — FE mirror of services/sbom_conformance.CHECK_IDS", () => {
  // Same latent-drift class as scan kinds: the conformance panel renders each
  // check label through a dynamic `conformance.check_id.${id}` key and the
  // FE mirror constant `SBOM_CHECK_IDS` drives nothing structurally but pins
  // the canonical id set + order against the backend. A check added on the BE
  // would otherwise render only the backend-supplied `check.label` fallback
  // (no localized string, no KO mirror) and slip through.
  const RESULTS = ["pass", "warn", "fail"] as const;

  it("matches the backend's check id set, in canonical order", () => {
    expect([...SBOM_CHECK_IDS]).toEqual([
      "timestamp",
      "tools",
      "top-component",
      "name-version",
      "purl",
      "no-generic",
      "transitive",
      "license",
      "hash",
    ]);
  });

  it.each([
    ["en", enScans],
    ["ko", koScans],
  ])("every check id owns a %s `conformance.check_id.*` label", (_locale, ns) => {
    const labels = labelMap(ns, "conformance", "check_id");
    for (const id of SBOM_CHECK_IDS) {
      expect(labels[id], `conformance.check_id.${id} missing`).toBeTruthy();
    }
  });

  it.each([
    ["en", enScans],
    ["ko", koScans],
  ])("every result owns a %s `conformance.result.*` label", (_locale, ns) => {
    const labels = labelMap(ns, "conformance", "result");
    for (const result of RESULTS) {
      expect(labels[result], `conformance.result.${result} missing`).toBeTruthy();
    }
  });
});

describe("vulnerability statuses — label-map half of the 7-state mirror", () => {
  it.each([
    ["en", enProjectDetail],
    ["ko", koProjectDetail],
  ])(
    "every VEX state owns a %s `vulnerabilities.status.*` label",
    (_locale, ns) => {
      const statuses = labelMap(ns, "vulnerabilities", "status");
      for (const status of ALL_VULNERABILITY_STATUSES) {
        expect(
          statuses[status],
          `vulnerabilities.status.${status} missing`,
        ).toBeTruthy();
      }
    },
  );
});
