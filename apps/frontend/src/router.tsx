import { Navigate, Route, Routes } from "react-router-dom";

import { RequireAuth } from "@/components/RequireAuth";
import { AdminAuditPage } from "@/features/admin/audit/AdminAuditPage";
import { AdminDiskPage } from "@/features/admin/disk/AdminDiskPage";
import { AdminDTPage } from "@/features/admin/dt/AdminDTPage";
import { AdminHealthPage } from "@/features/admin/health/AdminHealthPage";
import { AdminLayout } from "@/features/admin/AdminLayout";
import { AdminNotFound } from "@/features/admin/AdminNotFound";
import { AdminScansPage } from "@/features/admin/scans/AdminScansPage";
import { AdminTeamsPage } from "@/features/admin/teams/AdminTeamsPage";
import { AdminUsersPage } from "@/features/admin/users/AdminUsersPage";
import { ProjectDetailPage } from "@/features/projects/ProjectDetailPage";
import { ProjectListPage } from "@/features/projects/ProjectListPage";
import { Home } from "@/pages/Home";
import { ForgotPasswordPage } from "@/pages/auth/ForgotPasswordPage";
import { LoginPage } from "@/pages/auth/LoginPage";
import { RegisterPage } from "@/pages/auth/RegisterPage";

/**
 * Central route table — CLAUDE.md "Routing" convention.
 *
 * - Public auth pages live under /login, /register, /forgot-password.
 * - Authenticated pages are wrapped with <RequireAuth />.
 * - Admin pages nest under <AdminLayout /> which itself enforces the
 *   super-admin existence-hide guard. Non-super-admins see a 404 instead of
 *   a 403 — matching the backend's `require_super_admin_or_404` behavior.
 * - Unknown top-level routes land back on /login.
 */
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/forgot-password" element={<ForgotPasswordPage />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <Home />
          </RequireAuth>
        }
      />
      <Route
        path="/projects"
        element={
          <RequireAuth>
            <ProjectListPage />
          </RequireAuth>
        }
      />
      <Route
        path="/projects/:id"
        element={
          <RequireAuth>
            <ProjectDetailPage />
          </RequireAuth>
        }
      />
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
        <Route path="*" element={<AdminNotFound />} />
      </Route>
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}
