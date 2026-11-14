// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Theme preference control (W18). Cycles light -> dark -> system.
 *
 * Two shapes: a menu row for the account menu (the only place it is used
 * today) and a plain button. The cycle logic and the labels live here either
 * way, so the two cannot drift.
 */
import {
  DeviceDesktopIcon,
  MoonIcon,
  SunIcon,
  type Icon,
} from "@primer/octicons-react";
import { ActionList, Button } from "@primer/react";
import { useTranslation } from "react-i18next";

import { useTheme } from "@/hooks/useTheme";
import type { ThemePreference } from "@/lib/theme";

const NEXT: Record<ThemePreference, ThemePreference> = {
  light: "dark",
  dark: "system",
  system: "light",
};

const ICON: Record<ThemePreference, Icon> = {
  light: SunIcon,
  dark: MoonIcon,
  system: DeviceDesktopIcon,
};

interface ThemeToggleProps {
  /** Render as a row of the account menu instead of a standalone button. */
  inMenu?: boolean;
}

export function ThemeToggle({ inMenu = false }: ThemeToggleProps) {
  const { t } = useTranslation();
  const { preference, theme, setPreference } = useTheme();
  const Icon = ICON[preference];
  const label = `${t("theme.label")}: ${t(`theme.${preference}`)}`;
  const state = {
    "data-testid": "theme-toggle",
    "data-theme-preference": preference,
    "data-theme-resolved": theme,
    "aria-label": label,
    title: t(`theme.next.${NEXT[preference]}`),
  };

  if (inMenu) {
    return (
      <ActionList.Item
        {...state}
        onSelect={(event) => {
          // A cycle, not a destination: the menu stays open so the second
          // press does not need a fresh trip through the trigger.
          event.preventDefault();
          setPreference(NEXT[preference]);
        }}
      >
        <ActionList.LeadingVisual>
          <Icon size={16} />
        </ActionList.LeadingVisual>
        {t("theme.label")}
        <ActionList.TrailingVisual>
          {t(`theme.${preference}`)}
        </ActionList.TrailingVisual>
      </ActionList.Item>
    );
  }

  return (
    <Button
      {...state}
      size="small"
      leadingVisual={Icon}
      onClick={() => setPreference(NEXT[preference])}
    >
      {t(`theme.${preference}`)}
    </Button>
  );
}
