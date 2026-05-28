/**
 * DemoBanner — v2.1 Track B (B5).
 *
 * A slim, dismissable-free banner shown at the top of the authenticated shell
 * when the backend reports `demo_read_only`. It tells the visitor that writes
 * are disabled so a 403 from the read-only middleware is never a surprise. Kept
 * intentionally minimal (one line, one icon) per the "don't over-do it" brief.
 *
 * Rendering is gated on the backend flag (see useDemoMode) so a normal deploy
 * shows nothing and ships no extra chrome.
 */
import { Eye } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useDemoMode } from "@/hooks/useDemoMode";

export function DemoBanner() {
  const { t } = useTranslation("common");
  const { demoReadOnly } = useDemoMode();

  if (!demoReadOnly) {
    return null;
  }

  return (
    <div
      role="status"
      data-testid="demo-banner"
      className="flex items-center justify-center gap-2 border-b border-amber-300 bg-amber-50 px-4 py-1.5 text-xs font-medium text-amber-900"
    >
      <Eye className="h-3.5 w-3.5 shrink-0" aria-hidden />
      <span>
        <span className="font-semibold">{t("demo.banner_title")}</span>
        <span className="mx-1.5 text-amber-400" aria-hidden>
          ·
        </span>
        {t("demo.banner_detail")}
      </span>
    </div>
  );
}
