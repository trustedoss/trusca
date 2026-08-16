// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { Monitor, Moon, Sun } from "lucide-react";
import { forwardRef, type ComponentPropsWithoutRef } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { useTheme } from "@/hooks/useTheme";
import type { ThemePreference } from "@/lib/theme";
import { cn } from "@/lib/utils";

/**
 * Theme switcher (W18) — cycles light → dark → system.
 *
 * A three-state cycle rather than a two-state switch, because `system` is a
 * real answer and dropping it would mean a user who wants the app to follow
 * their OS has no way to say so once they have touched the control. Three
 * states in a menu would be more discoverable, but this sits in a 56 px bar
 * next to the language switcher, which is also a cycle — matching it keeps
 * the bar one idiom instead of two.
 *
 * The icon shows the CURRENT state, not the next one, for the reason recorded
 * in `LanguageToggle`: showing the target read as a statement about where you
 * already were.
 */
const NEXT: Record<ThemePreference, ThemePreference> = {
  light: "dark",
  dark: "system",
  system: "light",
};

const ICON: Record<ThemePreference, typeof Sun> = {
  light: Sun,
  dark: Moon,
  system: Monitor,
};

interface ThemeToggleProps extends ComponentPropsWithoutRef<typeof Button> {
  /** Rendered on the dark global bar - use the topbar foreground scale. */
  onInk?: boolean;
  /** Rendered as a row inside the account menu (C1). */
  inMenu?: boolean;
}

/** forwardRef for the same reason as `LanguageToggle` - see the note there. */
export const ThemeToggle = forwardRef<HTMLButtonElement, ThemeToggleProps>(
  function ThemeToggle(
    { onInk = false, inMenu = false, className, onClick, ...rest },
    ref,
  ) {
  const { t } = useTranslation();
  const { preference, theme, setPreference } = useTheme();
  const Icon = ICON[preference];

  return (
    <Button
      {...rest}
      ref={ref}
      variant={onInk || inMenu ? "ghost" : "outline"}
      size="sm"
      className={cn(
        onInk &&
          "text-topbar-muted-foreground hover:bg-topbar-accent hover:text-topbar-foreground",
        // C1: as a row in the account menu it has to look like the rows
        // above it, not like a button someone dropped into a menu.
        inMenu && "h-8 w-full justify-start font-normal",
        className,
      )}
      // Pulled out of `rest` and called after our own work rather than left
      // to the spread: the parent's handler here is the one Radix uses to
      // fire `onSelect`, and letting either declaration win outright loses
      // the other. The menu row needs both.
      onClick={(event) => {
        setPreference(NEXT[preference]);
        onClick?.(event);
      }}
      data-testid="theme-toggle"
      data-theme-preference={preference}
      data-theme-resolved={theme}
      // The label names what the control does and what it would do next, so a
      // screen reader user is not left to infer the cycle from an icon.
      aria-label={`${t("theme.label")}: ${t(`theme.${preference}`)}`}
      title={t(`theme.next.${NEXT[preference]}`)}
    >
      <Icon className="h-4 w-4" aria-hidden />
      {inMenu ? (
        <>
          <span>{t("theme.label")}</span>
          <span className="ml-auto text-xs text-muted-foreground">
            {t(`theme.${preference}`)}
          </span>
        </>
      ) : (
        <span className="sr-only sm:not-sr-only">
          {t(`theme.${preference}`)}
        </span>
      )}
    </Button>
  );
});
