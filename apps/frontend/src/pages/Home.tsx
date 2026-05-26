import { Navigate } from "react-router-dom";

/**
 * Legacy safety-net redirect. The app index "/" now renders the Dashboard
 * directly (see router.tsx); this component is no longer wired into the route
 * table but is kept pointing at the dashboard so any future re-use stays
 * consistent with the screen spec.
 */
export function Home() {
  return <Navigate to="/" replace />;
}
