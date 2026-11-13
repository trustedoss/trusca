/**
 * Ids of the representative screens, as plain data.
 *
 * Split out from `representativeScreens.ts` so unit tests can import the
 * list without pulling in Playwright: the register's `visit` functions
 * depend on `PortalPage`, which imports `@playwright/test` at runtime and
 * has no business loading inside vitest.
 *
 * The register types itself against these ids, so a screen added here
 * without a navigation function fails `tsc`, not review.
 */

export const LOGIN_SCREEN_ID = "login";

export const AUTHENTICATED_SCREEN_IDS = [
  "projects-list",
  "project-detail-overview",
  "project-detail-vulnerabilities",
  "dashboard",
  "scans",
  "approvals",
  "admin-users",
  "project-detail-components",
  "project-detail-compliance",
  "project-detail-settings",
  "policies",
  "integrations",
  "notifications",
] as const;

export type AuthenticatedScreenId = (typeof AUTHENTICATED_SCREEN_IDS)[number];

/**
 * The subset the 390 px overflow gate (`tests/responsive/narrow.spec.ts`)
 * walks. It was the whole visual set until six screens were added to take
 * baselines ahead of the component-library migration. Two of those
 * (project settings, notifications) show no figures, and the narrow gate's
 * widened pass fails a screen with no numeric text by design, so the new
 * screens are not walked here. Whether they should be is a separate
 * decision about the narrow gate, not about the baselines.
 */
export const NARROW_SCREEN_IDS = [
  "projects-list",
  "project-detail-overview",
  "project-detail-vulnerabilities",
  "dashboard",
  "scans",
  "approvals",
  "admin-users",
] as const satisfies readonly AuthenticatedScreenId[];

/** Every screen id the gates walk, login included. */
export const ALL_SCREEN_IDS: string[] = [
  LOGIN_SCREEN_ID,
  ...AUTHENTICATED_SCREEN_IDS,
];

/**
 * Screens the accessibility scan walks in addition to `AUTHENTICATED_SCREEN_IDS`
 * (#425). Deliberately NOT part of `ALL_SCREEN_IDS`: that register also drives
 * the visual-regression baseline set via `visualCoverage.test.ts`, and axe
 * violations are a property of the markup, not the pixels: a screen that
 * shares another screen's layout template (and so earns no baseline of its
 * own under `coverage-manifest.ts`'s reasoning) can still carry its own
 * missing labels or focus-order defects. Coupling the two registers would
 * mean choosing between an inflated visual-baseline set (the maintenance
 * cost `coverage-manifest.ts` was written to avoid) or leaving these screens
 * unscanned, which is the gap this issue is about.
 *
 * Left out, with the same reasoning `coverage-manifest.ts` already applies:
 *   - ScanDetailPage: log stream, inherently volatile content.
 *   - AdminDiskPage / AdminHealthPage / AdminBackupPage: live, volatile panels.
 *   - NotificationsPage: feed content, volatile.
 *   - DesignSystemPreview: dev-only, tree-shaken from production builds.
 *   - RegisterPage / ForgotPasswordPage / ResetPasswordPage: identical
 *     auth-card markup to LoginPage, which is already scanned.
 *   - NotFoundPage / AdminNotFound: minimal content, already asserted
 *     behaviourally by their own dedicated tests.
 *   - ComparePage / ComponentDetailPage / VulnerabilityDetailPage /
 *     IntakeRequestsPage: dual-surface or deployment-gated; left for a
 *     follow-up rather than growing this list further in one pass.
 */
export const A11Y_ONLY_SCREEN_IDS = [
  "project-create",
  "search",
  "inventory",
  "groups",
  "group-detail",
  "profile",
  "about",
  "admin-teams",
  "admin-scans",
  "admin-audit",
  "external-package-lookup",
] as const;

export type A11yOnlyScreenId = (typeof A11Y_ONLY_SCREEN_IDS)[number];
