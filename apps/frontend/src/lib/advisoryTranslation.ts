/**
 * Advisory translations for backend-owned license content (C1a).
 *
 * The UI chrome is translated by react-i18next from the locale files, but
 * license summaries and obligation prose are *content* the backend owns: it
 * returns the English text plus an advisory Korean rendering (`*_ko`) for the
 * finite classification catalog. This hook centralises the one rule both
 * surfaces need — show Korean when the user reads Korean AND a rendering
 * exists, otherwise fall back to English — so no call site re-derives it.
 *
 * English stays authoritative. When a translation is shown, `original` carries
 * the English text so the surface can offer it (the canonical license text is
 * never translated; the Korean rendering is a reading aid).
 */
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

export interface AdvisoryText {
  /** The text to render. */
  text: string;
  /** The English original — non-null only when `text` is a translation. */
  original: string | null;
}

export interface AdvisoryTranslation {
  /** Resolve one English/Korean pair to the text this reader should see. */
  pick: (en: string, ko: string | null | undefined) => AdvisoryText;
  /** True when the active language is Korean (regardless of availability). */
  prefersKorean: boolean;
}

export function useAdvisoryTranslation(): AdvisoryTranslation {
  const { i18n } = useTranslation();
  // `resolvedLanguage` is what i18next actually renders (it accounts for the
  // fallback chain); `language` can still be a region-tagged request like
  // "ko-KR", so compare on the base subtag.
  const active = i18n.resolvedLanguage ?? i18n.language ?? "en";
  const prefersKorean = active.split("-")[0] === "ko";

  return useMemo(
    () => ({
      prefersKorean,
      pick: (en, ko) =>
        prefersKorean && ko ? { text: ko, original: en } : { text: en, original: null },
    }),
    [prefersKorean],
  );
}
