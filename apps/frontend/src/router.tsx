import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/AppShell";
import { RequireAuth } from "@/components/RequireAuth";
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
 * - All authenticated pages nest inside <AppShell /> via <RequireAuth />.
 *   AppShell renders the 48px header + 224px sidebar + <Outlet />.
 * - The "/" index redirects to /projects; Home.tsx handles the same redirect
 *   as a safety net for any legacy deep link.
 * - Admin pages nest under <AdminLayout /> which enforces the super-admin
 *   existence-hide guard (404 for non-super-admins, matching backend behavior).
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
        <Route index element={<Navigate to="/projects" replace />} />
        <Route path="projects" element={<ProjectListPage />} />
        <Route path="projects/new" element={<ProjectCreatePage />} />
        <Route path="projects/:id" element={<ProjectDetailPage />} />
        <Route path="scans" element={<ScansPage />} />
        <Route path="approvals" element={<ApprovalsPage />} />
        <Route path="integrations" element={<IntegrationsPage />} />
        <Route path="notifications" element={<NotificationsPage />} />
      </Route>

      {/* Admin section retains its own layout (existence-hide guard inside) */}
      <Route
        path="/admin"
        element={
          <RequireAuth>
            <AdminLayout />
          </RequireAuth>
        }
      >
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

      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}
