// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { Languages } from "lucide-react";
import { forwardRef, type ComponentPropsWithoutRef } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import type { SupportedLanguage } from "@/lib/i18n";
import { cn } from "@/lib/utils";

interface LanguageToggleProps extends ComponentPropsWithoutRef<typeof Button> {
  /** Rendered on the dark global bar - use the topbar foreground scale. */
  onInk?: boolean;
  /** Rendered as a row inside the account menu (C1). */
  inMenu?: boolean;
}

/**
 * forwardRef because the account menu renders this through `asChild`: Radix
 * registers the item in its roving-focus group by ref, and a component it
 * cannot get a ref to is reachable by mouse and by nothing else.
 */
export const LanguageToggle = forwardRef<
  HTMLButtonElement,
  LanguageToggleProps
>(function LanguageToggle(
  { onInk = false, inMenu = false, className, ...rest },
  ref,
) {
  const { i18n, t } = useTranslation();
  const current = (i18n.resolvedLanguage ?? "en") as SupportedLanguage;
  const next: SupportedLanguage = current === "en" ? "ko" : "en";

  function handleToggle() {
    void i18n.changeLanguage(next);
  }

  // Show the CURRENT language (with the globe icon signalling it's a switcher).
  // Showing the *target* language read as "you are in that language" and
  // confused users into thinking the UI was set to the language on the button.
  const label = t(`language.${current === "en" ? "english" : "korean"}`);

  return (
    <Button
      {...rest}
      ref={ref}
      variant={onInk || inMenu ? "ghost" : "outline"}
      size="sm"
      className={cn(
        onInk &&
          "text-topbar-muted-foreground hover:bg-topbar-accent hover:text-topbar-foreground",
        // C1: as a row in the account menu, matching the rows above it.
        inMenu && "h-8 w-full justify-start font-normal",
        className,
      )}
      onClick={handleToggle}
      data-testid="language-toggle"
      data-current-language={current}
      aria-label={t("language.label")}
    >
      <Languages className="h-4 w-4" aria-hidden />
      {inMenu ? (
        <>
          <span>{t("language.label")}</span>
          <span className="ml-auto text-xs text-muted-foreground">{label}</span>
        </>
      ) : (
        <span>{label}</span>
      )}
    </Button>
  );
});
