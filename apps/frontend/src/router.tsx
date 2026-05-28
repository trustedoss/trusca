import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/AppShell";
import { RequireAuth } from "@/components/RequireAuth";
import { DashboardPage } from "@/features/dashboard/DashboardPage";
import { AdminAuditPage } from "@/features/admin/audit/AdminAuditPage";
import { AdminBackupPage } from "@/features/admin/backup/AdminBackupPage";
import { AdminDiskPage } from "@/features/admin/disk/AdminDiskPage";
import { AdminHealthPage } from "@/features/admin/health/AdminHealthPage";
import { AdminLayout } from "@/features/admin/AdminLayout";
import { AdminNotFound } from "@/features/admin/AdminNotFound";
import { AdminScansPage } from "@/features/admin/scans/AdminScansPage";
import { AdminTeamsPage } from "@/features/admin/teams/AdminTeamsPage";
import { AdminUsersPage } from "@/features/admin/users/AdminUsersPage";
import { ApprovalsPage } from "@/features/approvals/ApprovalsPage";
import { IntegrationsPage } from "@/features/integrations/IntegrationsPage";
import { NotificationsPage } from "@/features/notifications/NotificationsPage";
import { PoliciesPage } from "@/features/policies/PoliciesPage";
import { UserProfilePage } from "@/features/profile/UserProfilePage";
import { ComparePage } from "@/features/projects/ComparePage";
import { ProjectCreatePage } from "@/features/projects/ProjectCreatePage";
import { ProjectDetailPage } from "@/features/projects/ProjectDetailPage";
import { ProjectListPage } from "@/features/projects/ProjectListPage";
import { ComponentDetailPage } from "@/features/projects/pages/ComponentDetailPage";
import { VulnerabilityDetailPage } from "@/features/projects/pages/VulnerabilityDetailPage";
import { ScansPage } from "@/features/scans/ScansPage";
import { ForgotPasswordPage } from "@/pages/auth/ForgotPasswordPage";
import { LoginPage } from "@/pages/auth/LoginPage";
import { RegisterPage } from "@/pages/auth/RegisterPage";
import { ResetPasswordPage } from "@/pages/auth/ResetPasswordPage";
// W11-A — dev-only design system preview. Production builds short-circuit
// the route below via `import.meta.env.DEV`, so the component is tree-shaken
// out of the prod bundle entirely.
import { DesignSystemPreview } from "@/pages/dev/DesignSystemPreview";

/**
 * Central route table — CLAUDE.md "Routing" convention.
 *
 * - Public auth pages live under /login, /register, /forgot-password.
 * - All authenticated pages — including /admin/* — nest inside <AppShell />
 *   via <RequireAuth />. AppShell owns the only sidebar + header chrome and
 *   already renders the admin nav section for super-admins, so entering the
 *   admin area no longer unmounts the main nav (W4-A fix).
 * - The "/" index renders a dedicated <DashboardPage />. The previous
 *   redirect-to-/projects shortcut (audit D1-001 in W9) was our lone gap
 *   vs. every audited competitor (BD Polaris, Snyk, Sonatype, Mend,
 *   Datadog CSM all open on a portfolio dashboard, not a list).
 * - <AdminLayout /> wraps /admin/* with the super-admin existence-hide guard
 *   (404 for non-super-admins, matching backend behavior). It no longer
 *   renders its own chrome — the AppShell sidebar/header carries through.
 * - Unknown top-level routes fall back to /login.
 */
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/forgot-password" element={<ForgotPasswordPage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />

      {/* W11-A — design system preview, dev only. Production builds fall
       * through to the `*` redirect at the bottom (→ /login). */}
      {import.meta.env.DEV ? (
        <Route path="/dev/design-preview" element={<DesignSystemPreview />} />
      ) : null}

      {/* Authenticated app shell — sidebar + header wrap all app routes */}
      <Route
        path="/"
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route index element={<DashboardPage />} />
        <Route path="projects" element={<ProjectListPage />} />
        <Route path="projects/new" element={<ProjectCreatePage />} />
        <Route path="projects/:id" element={<ProjectDetailPage />} />
        <Route path="projects/:id/compare" element={<ComparePage />} />
        {/*
         * W10-B — dedicated full-page surface for a single vulnerability
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
         * W10-E — dedicated full-page surface for a single component.
         * Complements the existing drawer surface at
         * `/projects/:id?tab=components&drawer=<id>` (still supported for
         * backward-compat). Mirrors the W10-B vulnerabilities-page pattern.
         */}
        <Route
          path="projects/:projectId/components/:componentId"
          element={<ComponentDetailPage />}
        />
        <Route path="scans" element={<ScansPage />} />
        <Route path="approvals" element={<ApprovalsPage />} />
        <Route path="policies" element={<PoliciesPage />} />
        <Route path="integrations" element={<IntegrationsPage />} />
        <Route path="notifications" element={<NotificationsPage />} />
        <Route path="profile" element={<UserProfilePage />} />

        {/* Admin section — nested so AppShell chrome persists; AdminLayout
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
      </Route>

      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}
