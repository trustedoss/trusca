import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import enAuth from "@/locales/en/auth.json";
import enCommon from "@/locales/en/common.json";
import koAuth from "@/locales/ko/auth.json";
import koCommon from "@/locales/ko/common.json";

export const SUPPORTED_LANGUAGES = ["en", "ko"] as const;
export type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number];

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      en: { common: enCommon, auth: enAuth },
      ko: { common: koCommon, auth: koAuth },
    },
    fallbackLng: "en",
    supportedLngs: SUPPORTED_LANGUAGES,
    defaultNS: "common",
    ns: ["common", "auth"],
    interpolation: { escapeValue: false },
    detection: {
      order: ["localStorage", "navigator"],
      caches: ["localStorage"],
      lookupLocalStorage: "trustedoss.lang",
    },
  });

export default i18n;
