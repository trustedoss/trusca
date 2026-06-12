import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "@/App";
import { AppProviders } from "@/components/AppProviders";
import "@/lib/i18n";
import "@/index.css";

if (import.meta.env.DEV) {
  // AIS theme prototype (dev only) — dynamic imports keep the CSS chunk and
  // the helper module out of production builds entirely; Vite replaces DEV
  // with `false` and drops the whole branch.
  void import("@/styles/theme-ais.css");
  void import("@/lib/devTheme").then((m) => m.initAisTheme());
}

const container = document.getElementById("root");
if (!container) {
  throw new Error("Root container #root not found");
}

createRoot(container).render(
  <StrictMode>
    <AppProviders>
      <App />
    </AppProviders>
  </StrictMode>,
);
