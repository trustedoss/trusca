/**
 * G0-2 — visual coverage manifest.
 *
 * Every screen the router can render is classified here, exactly once:
 * either a baseline guards it, or it is deliberately exempt with a reason.
 * `tests/unit/design/visualCoverage.test.ts` diffs these keys against the
 * components `router.tsx` actually mounts, so a new screen cannot land
 * without someone making that call.
 *
 * Why classification instead of "snapshot everything"
 * ---------------------------------------------------
 * The original spec kept 4 of ~22 screens on purpose: every baseline is a
 * maintenance liability, and a wall of flaky diffs teaches reviewers to
 * skim past red. That reasoning still holds. What was missing was not
 * coverage — it was a record of the decision, so screens added later
 * silently defaulted to "unwatched".
 *
 * Represented screens were chosen to cover the distinct layout templates
 * the console work will disturb: auth, portfolio dashboard, list, detail
 * with tabs, virtualized table, workflow queue, and the admin section.
 * A change to the shell chrome (sidebar, header) shows up in all of them
 * at once, which is the point.
 */

/** A screen guarded by one or more committed baseline PNGs. */
interface Represented {
  /** One entry per baseline — a tabbed screen may need several. */
  snapshots: string[];
  /** What this entry is meant to catch that the others would not. */
  covers: string;
}

/** A screen deliberately left unguarded. */
interface Exempt {
  exempt: string;
}

export type VisualCoverage = Represented | Exempt;

export const VISUAL_COVERAGE: Record<string, VisualCoverage> = {
  // --- Auth -------------------------------------------------------------
  LoginPage: {
    snapshots: ["login.png"],
    covers: "Pre-auth template — the only surface with no shell chrome.",
  },
  RegisterPage: { exempt: "Same auth card template as LoginPage." },
  ForgotPasswordPage: { exempt: "Same auth card template as LoginPage." },
  ResetPasswordPage: { exempt: "Same auth card template as LoginPage." },

  // --- Portfolio --------------------------------------------------------
  DashboardPage: {
    snapshots: ["dashboard.png"],
    covers:
      "KPI tiles + charts. The first screen after login and the surface the " +
      "cockpit rework replaces, so drift here is drift in the product's face.",
  },
  ProjectListPage: {
    snapshots: ["projects-list.png"],
    covers: "Table list template with row density and status columns.",
  },
  SearchPage: {
    exempt:
      "Tabs + inline filter bar + paginated rows — the same three templates " +
      "ScansPage and the project detail tabs already guard; nothing here is " +
      "chrome the baselines have not seen.",
  },
  InventoryPage: {
    exempt:
      "Same virtualized table + inline filter-bar template as the project " +
      "detail Components tab, which project-detail-overview.png already " +
      "guards; the only novel chrome is the summary strip, a single line of " +
      "muted text.",
  },
  ProjectCreatePage: {
    exempt:
      "Narrow centred form (max-w-lg); shell chrome around it is already " +
      "covered by every other authenticated entry.",
  },
  ProjectDetailPage: {
    snapshots: [
      "project-detail-overview.png",
      "project-detail-vulnerabilities.png",
    ],
    covers:
      "Tabbed detail template — the risk gauge on the overview tab, and " +
      "the virtualized table on the vulnerabilities tab. One component, " +
      "two layouts that fail independently.",
  },
  ComparePage: { exempt: "Diff table variant of the list template." },
  VulnerabilityDetailPage: {
    exempt:
      "W10 dual-surface page; the same data renders in the drawer that the " +
      "vulnerabilities-tab baseline already captures.",
  },
  ComponentDetailPage: {
    exempt: "W10 dual-surface page — mirrors VulnerabilityDetailPage.",
  },

  // --- Operations -------------------------------------------------------
  ScansPage: {
    snapshots: ["scans.png"],
    covers:
      "Status-pill vocabulary across all scan states — the surface that " +
      "would show a regression in the status token family first.",
  },
  ScanDetailPage: { exempt: "Log stream; content is inherently volatile." },
  ApprovalsPage: {
    snapshots: ["approvals.png"],
    covers:
      "Workflow queue template — the governance surface BomLens has no " +
      "equivalent of, and the one the differentiation work leans on.",
  },
  IntakeRequestsPage: {
    exempt:
      "Off in every deployment that has not opted in, so a baseline would " +
      "capture the disabled notice rather than the queue, and capturing the " +
      "queue would mean seeding a deployment setting the other screens do " +
      "not need. The queue reuses the workflow-queue template ApprovalsPage " +
      "already guards.",
  },
  ExternalPackageLookupPage: {
    exempt:
      "Single-shot lookup form, no data-density layout to regress. Reuses " +
      "the same disabled-notice and EmptyState templates already guarded " +
      "elsewhere (IntakeRequestsPage, SearchPage).",
  },
  PoliciesPage: { exempt: "Settings form template." },
  IntegrationsPage: { exempt: "Settings form template." },
  NotificationsPage: { exempt: "Feed list; content volatile." },
  UserProfilePage: { exempt: "Settings form template." },
  AboutPage: {
    exempt:
      "Definition list plus a tabbed <pre> of license text — no layout template " +
      "the represented screens do not already cover, and the notice bodies are " +
      "long verbatim text that would make a baseline diff unreadable. Its " +
      "contract is asserted directly instead: AboutPage.test.tsx for the UI and " +
      "test_about_api.py for the bytes it serves.",
  },

  // --- Admin ------------------------------------------------------------
  AdminUsersPage: {
    snapshots: ["admin-users.png"],
    covers:
      "Admin section template — sidebar renders its admin group, and the " +
      "table carries role badges the rest of the app does not.",
  },
  AdminTeamsPage: { exempt: "Same admin list template as AdminUsersPage." },
  AdminScansPage: { exempt: "Same admin list template as AdminUsersPage." },
  AdminAuditPage: { exempt: "Same admin list template as AdminUsersPage." },
  AdminDiskPage: {
    exempt: "Panel content is live disk usage — volatile by nature.",
  },
  AdminHealthPage: {
    exempt:
      "Panels report feed freshness and DB timestamps that move between " +
      "runs; masking enough of it to be stable would leave little asserted.",
  },
  AdminBackupPage: { exempt: "Backup inventory is environment-dependent." },
  AdminNotFound: { exempt: "Existence-hide 404 stub, no layout of its own." },
  NotFoundPage: {
    exempt:
      "One centred block inside shell chrome the represented screens already " +
      "capture, with no data behind it and nothing that varies between runs. " +
      "What is worth guarding here is behaviour rather than pixels: that the " +
      "route table sends unknown paths at it and that it names the address " +
      "and offers a way out. NotFoundPage.test.tsx asserts both on every PR, " +
      "and tests/e2e/not_found.spec.ts walks the real navigation nightly " +
      "(the e2e job does not run on PRs).",
  },

  // --- Dev only ---------------------------------------------------------
  DesignSystemPreview: {
    exempt: "Dev-only route; tree-shaken out of production builds.",
  },
};

/**
 * Baseline file names this manifest claims, for the spec to cross-check.
 *
 * Each screen now has two: the light capture under its bare name, and the
 * dark one suffixed (W18). The suffix is derived here rather than listed
 * above, so adding a screen still means adding one line and cannot leave the
 * pair half-declared — which would read as "this screen is covered" while
 * one of its two themes had no baseline at all.
 */
export const REPRESENTED_SNAPSHOTS = Object.values(VISUAL_COVERAGE)
  .filter((entry): entry is Represented => "snapshots" in entry)
  .flatMap((entry) => entry.snapshots)
  .flatMap((name) => [name, name.replace(/\.png$/, "-dark.png")]);
