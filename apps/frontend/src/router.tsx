// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/AppShell";
import { RequireAuth } from "@/components/RequireAuth";
import { RouteLoadingFallback } from "@/components/RouteLoadingFallback";
import { DashboardPage } from "@/features/dashboard/DashboardPage";
import { ForgotPasswordPage } from "@/pages/auth/ForgotPasswordPage";
import { LoginPage } from "@/pages/auth/LoginPage";
import { RegisterPage } from "@/pages/auth/RegisterPage";
import { ResetPasswordPage } from "@/pages/auth/ResetPasswordPage";

// #421: every OTHER route is `React.lazy()`. `apps/frontend/dist/assets/
// index-*.js` was a single ~1.49 MB (502 KB gzip) chunk carrying all ~30
// screens whether or not a visit ever reached them, because router.tsx
// imported every one of them statically. The four auth pages above and the
// dashboard stay eager: they are what an anonymous visitor and a freshly
// signed-in one see FIRST, so splitting them would trade one guaranteed
// chunk fetch (the entry bundle, already paid for) for another (the lazy
// chunk) on the two most common navigations in the app. Every screen below
// this comment is reached by a click from inside the shell, where an extra
// chunk fetch is normal SPA behavior and the `<Suspense>` fallback below
// covers it.
const AdminAuditPage = lazy(() =>
  import("@/features/admin/audit/AdminAuditPage").then((m) => ({
    default: m.AdminAuditPage,
  })),
);
const AdminBackupPage = lazy(() =>
  import("@/features/admin/backup/AdminBackupPage").then((m) => ({
    default: m.AdminBackupPage,
  })),
);
const AdminDiskPage = lazy(() =>
  import("@/features/admin/disk/AdminDiskPage").then((m) => ({
    default: m.AdminDiskPage,
  })),
);
const AdminHealthPage = lazy(() =>
  import("@/features/admin/health/AdminHealthPage").then((m) => ({
    default: m.AdminHealthPage,
  })),
);
const AdminLayout = lazy(() =>
  import("@/features/admin/AdminLayout").then((m) => ({ default: m.AdminLayout })),
);
const AdminNotFound = lazy(() =>
  import("@/features/admin/AdminNotFound").then((m) => ({ default: m.AdminNotFound })),
);
const AdminScansPage = lazy(() =>
  import("@/features/admin/scans/AdminScansPage").then((m) => ({
    default: m.AdminScansPage,
  })),
);
const AdminTeamsPage = lazy(() =>
  import("@/features/admin/teams/AdminTeamsPage").then((m) => ({
    default: m.AdminTeamsPage,
  })),
);
const AdminUsersPage = lazy(() =>
  import("@/features/admin/users/AdminUsersPage").then((m) => ({
    default: m.AdminUsersPage,
  })),
);
const ApprovalsPage = lazy(() =>
  import("@/features/approvals/ApprovalsPage").then((m) => ({ default: m.ApprovalsPage })),
);
const ExternalPackageLookupPage = lazy(() =>
  import("@/features/external-package-lookup/ExternalPackageLookupPage").then((m) => ({
    default: m.ExternalPackageLookupPage,
  })),
);
const GroupDetailPage = lazy(() =>
  import("@/features/groups/GroupDetailPage").then((m) => ({ default: m.GroupDetailPage })),
);
const GroupListPage = lazy(() =>
  import("@/features/groups/GroupListPage").then((m) => ({ default: m.GroupListPage })),
);
const IntakeRequestsPage = lazy(() =>
  import("@/features/intake/IntakeRequestsPage").then((m) => ({
    default: m.IntakeRequestsPage,
  })),
);
const IntegrationsPage = lazy(() =>
  import("@/features/integrations/IntegrationsPage").then((m) => ({
    default: m.IntegrationsPage,
  })),
);
const InventoryPage = lazy(() =>
  import("@/features/inventory/InventoryPage").then((m) => ({ default: m.InventoryPage })),
);
const SearchPage = lazy(() =>
  import("@/features/search/SearchPage").then((m) => ({ default: m.SearchPage })),
);
const NotificationsPage = lazy(() =>
  import("@/features/notifications/NotificationsPage").then((m) => ({
    default: m.NotificationsPage,
  })),
);
const PoliciesPage = lazy(() =>
  import("@/features/policies/PoliciesPage").then((m) => ({ default: m.PoliciesPage })),
);
const AboutPage = lazy(() => import("@/features/about/AboutPage"));
const UserProfilePage = lazy(() =>
  import("@/features/profile/UserProfilePage").then((m) => ({ default: m.UserProfilePage })),
);
const ComparePage = lazy(() =>
  import("@/features/projects/ComparePage").then((m) => ({ default: m.ComparePage })),
);
const ProjectCreatePage = lazy(() =>
  import("@/features/projects/ProjectCreatePage").then((m) => ({
    default: m.ProjectCreatePage,
  })),
);
const ProjectDetailPage = lazy(() =>
  import("@/features/projects/ProjectDetailPage").then((m) => ({
    default: m.ProjectDetailPage,
  })),
);
const ProjectListPage = lazy(() =>
  import("@/features/projects/ProjectListPage").then((m) => ({ default: m.ProjectListPage })),
);
const ComponentDetailPage = lazy(() =>
  import("@/features/projects/pages/ComponentDetailPage").then((m) => ({
    default: m.ComponentDetailPage,
  })),
);
const VulnerabilityDetailPage = lazy(() =>
  import("@/features/projects/pages/VulnerabilityDetailPage").then((m) => ({
    default: m.VulnerabilityDetailPage,
  })),
);
const ScanDetailPage = lazy(() =>
  import("@/features/scan/ScanDetailPage").then((m) => ({ default: m.ScanDetailPage })),
);
const ScansPage = lazy(() =>
  import("@/features/scans/ScansPage").then((m) => ({ default: m.ScansPage })),
);
const NotFoundPage = lazy(() =>
  import("@/pages/NotFoundPage").then((m) => ({ default: m.NotFoundPage })),
);
// W11-A — dev-only design system preview. Production builds short-circuit
// the route below via `import.meta.env.DEV`, so the component is tree-shaken
// out of the prod bundle entirely regardless of eager/lazy; lazy anyway so a
// dev build's own entry chunk does not carry it either.
const DesignSystemPreview = lazy(() =>
  import("@/pages/dev/DesignSystemPreview").then((m) => ({ default: m.DesignSystemPreview })),
);

/**
 * Central route table — CLAUDE.md "Routing" convention.
 *
 * - Public auth pages live under /login, /register, /forgot-password.
 * - All authenticated pages — including /admin/* — nest inside <AppShell />
 *   via <RequireAuth />. AppShell owns the only sidebar + header chrome and
 *   already renders the admin nav section for super-admins, so entering the
 *   admin area no longer unmounts the main nav (W4-A fix).
 * - The "/" index renders a dedicated <DashboardPage />, replacing the
 *   previous redirect-to-/projects shortcut: a portfolio needs a landing
 *   surface that summarises it, not a list to scroll.
 * - <AdminLayout /> wraps /admin/* with the super-admin existence-hide guard
 *   (404 for non-super-admins, matching backend behavior). It no longer
 *   renders its own chrome — the AppShell sidebar/header carries through.
 * - Unknown routes land on <NotFoundPage />, nested inside the shell so the
 *   navigation survives the mistake. It sits under <RequireAuth />, so an
 *   anonymous visitor is still sent to /login and learns nothing about which
 *   paths exist.
 *
 * #421: every route below is `React.lazy()` except the four auth pages and
 * the dashboard (see the comment above the lazy() calls for why those four
 * stay eager). The single <Suspense> here covers all of them: one fallback
 * for the whole route tree rather than one per route, since a route change
 * already unmounts the previous page's content wholesale.
 */
export function AppRoutes() {
  return (
    <Suspense fallback={<RouteLoadingFallback />}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />

        {/* W11-A: design system preview, dev only. In production the path
         * matches nothing and falls through to the catch-all below. */}
        {import.meta.env.DEV ? (
          <Route path="/dev/design-preview" element={<DesignSystemPreview />} />
        ) : null}

        {/* Authenticated app shell: sidebar + header wrap all app routes */}
        <Route
          path="/"
          element={
            <RequireAuth>
              <AppShell />
            </RequireAuth>
          }
        >
          <Route index element={<DashboardPage />} />
          <Route path="components" element={<InventoryPage />} />
          <Route path="search" element={<SearchPage />} />
          <Route path="projects" element={<ProjectListPage />} />
          <Route path="projects/new" element={<ProjectCreatePage />} />
          {/* Group hierarchy Phase 4 PR 4-B: list (drill-down / flat search)
              + detail (breadcrumb, 30-day subtree stats, subgroups / projects
              / members). Nested under AppShell so the sidebar + header chrome
              persist, matching every other list/detail pair below. */}
          <Route path="groups" element={<GroupListPage />} />
          <Route path="groups/:groupId" element={<GroupDetailPage />} />
          <Route path="projects/:id" element={<ProjectDetailPage />} />
          <Route path="projects/:id/compare" element={<ComparePage />} />
          {/*
           * W10-B: dedicated full-page surface for a single vulnerability
           * finding. Complements the existing drawer surface at
           * `/projects/:id?tab=vulnerabilities&vuln=<id>` (still supported for
           * backward-compat). The route nests inside <AppShell /> so the
           * sidebar + header chrome persists; the page itself only owns the
           * breadcrumb + body region.
           */}
          <Route
            path="projects/:projectId/vulnerabilities/:findingId"
            element={<VulnerabilityDetailPage />}
          />
          {/*
           * W10-E: dedicated full-page surface for a single component.
           * Complements the existing drawer surface at
           * `/projects/:id?tab=components&drawer=<id>` (still supported for
           * backward-compat). Mirrors the W10-B vulnerabilities-page pattern.
           */}
          <Route
            path="projects/:projectId/components/:componentId"
            element={<ComponentDetailPage />}
          />
          <Route path="scans" element={<ScansPage />} />
          {/*
           * Dedicated full-page scan surface, replaces the cramped log panel
           * inside the right-side progress drawer with a real route the user
           * can deep-link, reload, and share. The drawer keeps the progress
           * summary + cancel affordance and links out here for the full log.
           */}
          <Route path="scans/:scanId" element={<ScanDetailPage />} />
          <Route path="approvals" element={<ApprovalsPage />} />
          {/* Always routed, even where the deployment has not turned the queue
              on: a bookmark should land somewhere that explains itself rather
              than on the not-found page. The page says so; the shell draws no
              entry point. */}
          <Route path="intake" element={<IntakeRequestsPage />} />
          {/* Same always-routed-even-when-off treatment as intake above. */}
          <Route path="packages/lookup" element={<ExternalPackageLookupPage />} />
          <Route path="policies" element={<PoliciesPage />} />
          <Route path="integrations" element={<IntegrationsPage />} />
          <Route path="notifications" element={<NotificationsPage />} />
          <Route path="profile" element={<UserProfilePage />} />
          {/* License notices, readable in the product: self-hosted and
              air-gapped installs cannot follow a link to GitHub. */}
          <Route path="about" element={<AboutPage />} />

          {/* Admin section: nested so AppShell chrome persists; AdminLayout
              still enforces the super-admin existence-hide guard. */}
          <Route path="admin" element={<AdminLayout />}>
            <Route index element={<Navigate to="users" replace />} />
            <Route path="users" element={<AdminUsersPage />} />
            <Route path="teams" element={<AdminTeamsPage />} />
            <Route path="scans" element={<AdminScansPage />} />
            <Route path="disk" element={<AdminDiskPage />} />
            <Route path="audit" element={<AdminAuditPage />} />
            <Route path="health" element={<AdminHealthPage />} />
            <Route path="backup" element={<AdminBackupPage />} />
            <Route path="*" element={<AdminNotFound />} />
          </Route>

          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </Suspense>
  );
}
