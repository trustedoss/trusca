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
] as const;

export type AuthenticatedScreenId = (typeof AUTHENTICATED_SCREEN_IDS)[number];

/** Every screen id the gates walk, login included. */
export const ALL_SCREEN_IDS: string[] = [
  LOGIN_SCREEN_ID,
  ...AUTHENTICATED_SCREEN_IDS,
];
