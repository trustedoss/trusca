// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { ThemeProvider } from "@primer/react/next";
import { useEffect, useState, type ReactNode } from "react";

import { readDocumentTheme, watchDocumentTheme, type ResolvedTheme } from "@/lib/theme";

interface Props {
  children: ReactNode;
}

/** Primer's colour mode names for our two themes. */
export function primerColorMode(theme: ResolvedTheme): "day" | "night" {
  return theme === "dark" ? "night" : "day";
}

/**
 * Primer's theme context for the whole app.
 *
 * The colour mode follows the `dark` class on <html>: theme state is a module
 * variable (hooks/useTheme.ts), not React state, so a MutationObserver is the
 * one place every switch is visible. `contextOnly` skips Primer's wrapper
 * element, because the three colour-mode attributes already sit on <html>
 * (lib/theme.ts) and cover portalled overlays as well.
 *
 * BaseStyles is deliberately not used. Its stylesheet sets global focus, link
 * and font rules that would change the Tailwind screens; Primer components get
 * our fonts and colours from src/primer.css instead.
 */
export default function PrimerRoot({ children }: Props) {
  const [theme, setTheme] = useState<ResolvedTheme>(() => readDocumentTheme());
  useEffect(() => {
    setTheme(readDocumentTheme());
    return watchDocumentTheme(setTheme);
  }, []);
  return (
    <ThemeProvider
      colorMode={primerColorMode(theme)}
      dayScheme="light"
      nightScheme="dark"
      contextOnly
    >
      {children}
    </ThemeProvider>
  );
}
