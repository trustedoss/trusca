import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import enAdmin from "@/locales/en/admin.json";
import enApprovals from "@/locales/en/approvals.json";
import enAuth from "@/locales/en/auth.json";
import enCommon from "@/locales/en/common.json";
import enIntegrations from "@/locales/en/integrations.json";
import enNotifications from "@/locales/en/notifications.json";
import enProjectDetail from "@/locales/en/project_detail.json";
import enProjects from "@/locales/en/projects.json";
import enScans from "@/locales/en/scans.json";
import koAdmin from "@/locales/ko/admin.json";
import koApprovals from "@/locales/ko/approvals.json";
import koAuth from "@/locales/ko/auth.json";
import koCommon from "@/locales/ko/common.json";
import koIntegrations from "@/locales/ko/integrations.json";
import koNotifications from "@/locales/ko/notifications.json";
import koProjectDetail from "@/locales/ko/project_detail.json";
import koProjects from "@/locales/ko/projects.json";
import koScans from "@/locales/ko/scans.json";

export const SUPPORTED_LANGUAGES = ["en", "ko"] as const;
export type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number];

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      en: {
        common: enCommon,
        auth: enAuth,
        projects: enProjects,
        project_detail: enProjectDetail,
        scans: enScans,
        admin: enAdmin,
        approvals: enApprovals,
        integrations: enIntegrations,
        notifications: enNotifications,
      },
      ko: {
        common: koCommon,
        auth: koAuth,
        projects: koProjects,
        project_detail: koProjectDetail,
        scans: koScans,
        admin: koAdmin,
        approvals: koApprovals,
        integrations: koIntegrations,
        notifications: koNotifications,
      },
    },
    fallbackLng: "en",
    supportedLngs: SUPPORTED_LANGUAGES,
    defaultNS: "common",
    ns: [
      "common",
      "auth",
      "projects",
      "project_detail",
      "scans",
      "admin",
      "approvals",
      "integrations",
      "notifications",
    ],
    interpolation: { escapeValue: false },
    detection: {
      order: ["localStorage", "navigator"],
      caches: ["localStorage"],
      lookupLocalStorage: "trustedoss.lang",
    },
  });

export default i18n;
