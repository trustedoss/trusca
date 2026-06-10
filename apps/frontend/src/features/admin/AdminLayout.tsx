/**
 * AdminLayout — super-admin existence-hide guard for `/admin/*`.
 *
 * Originally (Phase 4 PR #13/#14) this owned the full admin chrome — a
 * separate 224px sidebar + 48px header that replaced the AppShell. W4-A
 * stripped that chrome: the AppShell now persists across `/admin/*` and
 * renders the admin nav section in its sidebar for super-admins, so
 * entering admin no longer unmounts the main nav.
 *
 * What remains here is the security boundary: when the authenticated user
 * is not a super-admin, render the 404 shell instead of the outlet. This
 * matches the backend's existence-hide (404, not 403) for `/v1/admin/*`.
 * Anonymous visitors are still filtered out by the parent `<RequireAuth>`.
 */
import { Outlet } from "react-router-dom";

import { AdminNotFound } from "@/features/admin/AdminNotFound";
import { usePermissions } from "@/hooks/usePermissions";

export function AdminLayout() {
  const { isSuperAdmin } = usePermissions();

  if (!isSuperAdmin) {
    return <AdminNotFound />;
  }

  return (
    <div data-testid="admin-layout" className="flex-1">
      <Outlet />
    </div>
  );
}
