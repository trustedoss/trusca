import { AuthExpiredListener } from "@/components/AuthExpiredListener";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { AppRoutes } from "@/router";

export function App() {
  return (
    <ErrorBoundary>
      <AuthExpiredListener />
      <AppRoutes />
    </ErrorBoundary>
  );
}
