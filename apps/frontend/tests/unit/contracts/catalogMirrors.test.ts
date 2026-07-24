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
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { NOTIFICATION_KINDS } from "@/features/notifications/api/notificationsApi";
import { G7_CLUSTER_ORDER } from "@/features/scan/lib/g7Conformance";
import { KNOWN_OBLIGATION_KINDS } from "@/features/projects/api/obligationsApi";
import {
  CURRENCY_STATES,
  EOL_STATES,
} from "@/features/projects/api/projectDetailApi";
import { REVIEW_FLAG_VALUES } from "@/features/projects/api/licensesApi";
import { ALL_VULNERABILITY_STATUSES } from "@/features/projects/lib/vulnerabilityTransitions";
import { visualFor } from "@/features/projects/components/ProjectStatusBadge";
import {
  SBOM_CHECK_IDS,
  SBOM_REGULATORY_CHECK_IDS,
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
// Backend G7 registry — the FE cluster ORDER mirror must follow its cluster
// id order (same latent-drift class: the panel groups G7 checks by this list).
import g7Registry from "../../../../backend/services/g7_registry.json";

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
    // Sourced from `apps/backend/services/sbom_conformance.py::CHECK_IDS` —
    // the 9 original format checks plus the 5 regulatory field checks
    // (feat/sbom-conformance-crosswalk, verdict-neutral, CycloneDX only).
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
      "hash-algorithm",
      "component-creator",
      "component-filename",
      "artifact-uri",
      "file-properties",
    ]);
  });

  it("the regulatory subset mirrors REGULATORY_FIELD_CHECK_IDS and stays inside the full set", () => {
    expect([...SBOM_REGULATORY_CHECK_IDS]).toEqual([
      "hash-algorithm",
      "component-creator",
      "component-filename",
      "artifact-uri",
      "file-properties",
    ]);
    for (const id of SBOM_REGULATORY_CHECK_IDS) {
      expect(SBOM_CHECK_IDS).toContain(id);
    }
  });

  it.each([
    ["en", enScans],
    ["ko", koScans],
  ])("every check id owns a %s `conformance.check_id.*` label", (_locale, ns) => {
    const labels = labelMap(ns, "conformance", "check_id");
    // The 5 regulatory field checks intentionally render the backend-supplied
    // `check.label` (no FE localization — same convention as the G7 checks),
    // so the label contract covers the 9 core format checks only.
    const regulatory = new Set<string>(SBOM_REGULATORY_CHECK_IDS);
    for (const id of SBOM_CHECK_IDS) {
      if (regulatory.has(id)) continue;
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

describe("G7 clusters — FE mirror of services/g7_registry.json cluster order", () => {
  // feat/g7-conformance: `G7_CLUSTER_ORDER` drives the panel's cluster-card
  // ordering; the backend emits `check.cluster` values straight from the
  // registry. A cluster added/reordered on the BE side must fail here rather
  // than silently render at the end of the section (unknown clusters are
  // appended, never dropped — so the drift would be invisible in the UI).
  const registryClusterIds = (
    g7Registry.clusters as Array<{ id: string }>
  ).map((c) => c.id);

  it("G7_CLUSTER_ORDER equals the registry's cluster id order", () => {
    expect([...G7_CLUSTER_ORDER]).toEqual(registryClusterIds);
  });

  it.each([
    ["en", enScans],
    ["ko", koScans],
  ])(
    "every canonical cluster owns a %s `conformance.g7.cluster.*` label",
    (_locale, ns) => {
      const labels = labelMap(ns, "conformance", "g7", "cluster");
      for (const cluster of G7_CLUSTER_ORDER) {
        expect(
          labels[cluster],
          `conformance.g7.cluster.${cluster} missing`,
        ).toBeTruthy();
      }
    },
  );
});

describe("review flags — FE mirror of services/license_flags.REVIEW_FLAG_VALUES", () => {
  // Phase D: the AI license review-flag vocabulary lives in the backend
  // classifier (`services/license_flags.py::REVIEW_FLAG_VALUES`, the single
  // source of truth), the schema Literal, the router regex — and here, the FE
  // mirror that drives the review badge + the licenses filter select. Same
  // latent-drift class as the G7 clusters (§2): a token added on one side
  // without the other silently 422s a valid filter or advertises a value the
  // persistence layer never stores. We read the backend source verbatim and
  // extract the tuple so the guard can't drift out of sync with an import.
  function backendReviewFlagValues(): string[] {
    // Vitest root is apps/frontend, so the backend sits one level up. Reading
    // the source verbatim keeps the guard from drifting via a stale import.
    const src = readFileSync(
      resolve(process.cwd(), "../backend/services/license_flags.py"),
      "utf-8",
    );
    // Match `REVIEW_FLAG_VALUES: Final[tuple[str, str]] = ("a", "b")`.
    const assignment = /REVIEW_FLAG_VALUES[^=]*=\s*\(([^)]*)\)/.exec(src);
    if (!assignment) {
      throw new Error("REVIEW_FLAG_VALUES tuple not found in license_flags.py");
    }
    return [...assignment[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
  }

  it("REVIEW_FLAG_VALUES equals the backend classifier's tuple (set equality)", () => {
    expect(new Set(REVIEW_FLAG_VALUES)).toEqual(
      new Set(backendReviewFlagValues()),
    );
  });

  it.each([
    ["en", enProjectDetail],
  ])("every flag owns a %s short + description label", (_locale, ns) => {
    const short = labelMap(ns, "licenses", "review", "short");
    const description = labelMap(ns, "licenses", "review", "description");
    for (const flag of REVIEW_FLAG_VALUES) {
      expect(short[flag], `licenses.review.short.${flag} missing`).toBeTruthy();
      expect(
        description[flag],
        `licenses.review.description.${flag} missing`,
      ).toBeTruthy();
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

describe("EOL states — FE mirror of services.eol.eol_catalog.EOL_STATES", () => {
  // Phase M — the closed eol_state vocabulary lives twice: the backend
  // catalog (services/eol/eol_catalog.py EOL_STATES, persisted into
  // component_versions.eol_state) and the FE mirror EOL_STATES used by the
  // EolBadge / drawer labels. The backend half of this contract is
  // apps/backend/tests/unit/test_catalog_contracts.py.
  it("matches the backend's closed eol_state set, in canonical order", () => {
    expect([...EOL_STATES]).toEqual(["eol", "supported", "unknown"]);
  });

  it.each([
    ["en", enProjectDetail],
    ["ko", koProjectDetail],
  ])(
    "every eol state owns a %s `components.eol.state.*` label (plus untracked)",
    (_locale, ns) => {
      const states = labelMap(ns, "components", "eol", "state");
      for (const state of EOL_STATES) {
        expect(states[state], `components.eol.state.${state} missing`).toBeTruthy();
      }
      // The NULL (not-a-tracked-product) bucket renders through `untracked`.
      expect(states.untracked, "components.eol.state.untracked missing").toBeTruthy();
    },
  );
});

describe("currency states — FE mirror of services.eol.eol_catalog.CURRENCY_STATES", () => {
  // Version-currency sibling of the EOL vocabulary: the closed currency_state
  // set lives twice — the backend catalog (services/eol/eol_catalog.py
  // CURRENCY_STATES, persisted into component_versions.currency_state) and the
  // FE mirror CURRENCY_STATES used by the CurrencyBadge / drawer labels. Same
  // latent-drift guard class as EOL_STATES above.
  it("matches the backend's closed currency_state set, in canonical order", () => {
    expect([...CURRENCY_STATES]).toEqual(["current", "outdated", "unknown"]);
  });

  it.each([
    ["en", enProjectDetail],
    ["ko", koProjectDetail],
  ])(
    "every currency state owns a %s `components.currency.state.*` label (plus untracked)",
    (_locale, ns) => {
      const states = labelMap(ns, "components", "currency", "state");
      for (const state of CURRENCY_STATES) {
        expect(
          states[state],
          `components.currency.state.${state} missing`,
        ).toBeTruthy();
      }
      // The NULL (untracked) bucket renders through `untracked`.
      expect(
        states.untracked,
        "components.currency.state.untracked missing",
      ).toBeTruthy();
    },
  );
});

describe("C1a advisory-translation affordances — EN + KO parity", () => {
  // The advisory KO rendering of backend-owned license content (summaries +
  // obligation prose) needs one FE string per surface: a disclosure that
  // reveals the authoritative English original. Both must exist in both
  // locales, or a Korean reader gets a raw i18n key on the toggle.
  it.each([
    ["en", enProjectDetail],
    ["ko", koProjectDetail],
  ])("obligation drawer owns a %s translation_advisory label", (_locale, ns) => {
    const drawer = labelMap(ns, "obligations", "drawer");
    expect(
      drawer.translation_advisory,
      "obligations.drawer.translation_advisory missing",
    ).toBeTruthy();
  });

  it.each([
    ["en", enProjectDetail],
    ["ko", koProjectDetail],
  ])("license drawer owns a %s summary_advisory label", (_locale, ns) => {
    const drawer = labelMap(ns, "licenses", "drawer");
    expect(
      drawer.summary_advisory,
      "licenses.drawer.summary_advisory missing",
    ).toBeTruthy();
  });
});
