// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "@/App";
import { AppProviders } from "@/components/AppProviders";
import { initTheme } from "@/hooks/useTheme";
import "@/lib/i18n";
import "@/index.css";

const container = document.getElementById("root");
if (!container) {
  throw new Error("Root container #root not found");
}

// The inline script in index.html has already put the class on <html>; this
// tells the React side what it decided and subscribes to OS changes (W18).
initTheme();

createRoot(container).render(
  <StrictMode>
    <AppProviders>
      <App />
    </AppProviders>
  </StrictMode>,
);
