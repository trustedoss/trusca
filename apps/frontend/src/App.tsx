import { AuthExpiredListener } from "@/components/AuthExpiredListener";
import { AppRoutes } from "@/router";

export function App() {
  return (
    <>
      <AuthExpiredListener />
      <AppRoutes />
    </>
  );
}
