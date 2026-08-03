// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { Languages } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import type { SupportedLanguage } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export function LanguageToggle({
  onInk = false,
  className,
}: { onInk?: boolean; className?: string } = {}) {
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
      variant={onInk ? "ghost" : "outline"}
      size="sm"
      className={cn(
        onInk &&
          "text-topbar-muted-foreground hover:bg-topbar-accent hover:text-topbar-foreground",
        className,
      )}
      onClick={handleToggle}
      data-testid="language-toggle"
      data-current-language={current}
      aria-label={t("language.label")}
    >
      <Languages className="h-4 w-4" aria-hidden />
      <span>{label}</span>
    </Button>
  );
}
