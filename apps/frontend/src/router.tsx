import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/AppShell";
import { RequireAuth } from "@/components/RequireAuth";
import { DashboardPage } from "@/features/dashboard/DashboardPage";
import { AdminAuditPage } from "@/features/admin/audit/AdminAuditPage";
import { AdminBackupPage } from "@/features/admin/backup/AdminBackupPage";
import { AdminDiskPage } from "@/features/admin/disk/AdminDiskPage";
import { AdminDTPage } from "@/features/admin/dt/AdminDTPage";
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
import { ScansPage } from "@/features/scans/ScansPage";
import { ForgotPasswordPage } from "@/pages/auth/ForgotPasswordPage";
import { LoginPage } from "@/pages/auth/LoginPage";
import { RegisterPage } from "@/pages/auth/RegisterPage";
import { ResetPasswordPage } from "@/pages/auth/ResetPasswordPage";

/**
 * Central route table — CLAUDE.md "Routing" convention.
 *
 * - Public auth pages live under /login, /register, /forgot-password.
 * - All authenticated pages — including /admin/* — nest inside <AppShell />
 *   via <RequireAuth />. AppShell owns the only sidebar + header chrome and
 *   already renders the admin nav section for super-admins, so entering the
 *   admin area no longer unmounts the main nav (W4-A fix).
 * - The "/" index renders the Dashboard (org/team risk portfolio), the
 *   CLAUDE.md screen spec for "/". /projects remains its own route.
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
          <Route path="dt" element={<AdminDTPage />} />
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
