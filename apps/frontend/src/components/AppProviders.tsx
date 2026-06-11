import { QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { useState, type ReactNode } from "react";
import { BrowserRouter } from "react-router-dom";

import { ToastProvider } from "@/components/ui/toast";
import { createQueryClient } from "@/lib/queryClient";

interface AppProvidersProps {
  children: ReactNode;
  /**
   * Override the router for tests. Default uses BrowserRouter so the dev
   * server and production builds stay path-driven.
   */
  router?: "browser" | "none";
}

export function AppProviders({
  children,
  router = "browser",
}: AppProvidersProps) {
  const [queryClient] = useState(() => createQueryClient());

  const tree = (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>{children}</ToastProvider>
      {/* Devtools render in dev only; the unused branch is eliminated by Vite
          in production builds (`import.meta.env.DEV === false`). */}
      {import.meta.env.DEV ? (
        <ReactQueryDevtools
          initialIsOpen={false}
          buttonPosition="bottom-right"
        />
      ) : null}
    </QueryClientProvider>
  );

  if (router === "browser") {
    return <BrowserRouter>{tree}</BrowserRouter>;
  }
  return tree;
}
