// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Language toggle (W11). Two languages, so a switch rather than a list.
 *
 * Two shapes, like ThemeToggle: a row of the account menu, and a standalone
 * button for screens that have no menu (the sign-in layout).
 */
import { GlobeIcon } from "@primer/octicons-react";
import { ActionList, Button } from "@primer/react";
import { useTranslation } from "react-i18next";

import type { SupportedLanguage } from "@/lib/i18n";

interface LanguageToggleProps {
  /** Render as a row of the account menu instead of a standalone button. */
  inMenu?: boolean;
}

export function LanguageToggle({ inMenu = false }: LanguageToggleProps) {
  const { i18n, t } = useTranslation();
  const current = (i18n.resolvedLanguage ?? "en") as SupportedLanguage;
  const next: SupportedLanguage = current === "en" ? "ko" : "en";
  const label = t(`language.${current === "en" ? "english" : "korean"}`);
  const state = {
    "data-testid": "language-toggle",
    "data-current-language": current,
    "aria-label": t("language.label"),
  };

  if (inMenu) {
    return (
      <ActionList.Item
        {...state}
        onSelect={(event) => {
          event.preventDefault();
          void i18n.changeLanguage(next);
        }}
      >
        <ActionList.LeadingVisual>
          <GlobeIcon size={16} />
        </ActionList.LeadingVisual>
        {t("language.label")}
        <ActionList.TrailingVisual>{label}</ActionList.TrailingVisual>
      </ActionList.Item>
    );
  }

  return (
    <Button
      {...state}
      size="small"
      leadingVisual={GlobeIcon}
      onClick={() => void i18n.changeLanguage(next)}
    >
      {label}
    </Button>
  );
}
