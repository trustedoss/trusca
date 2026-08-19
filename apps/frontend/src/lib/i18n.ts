// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import { LANGUAGE_STORAGE_KEY } from "@/lib/languageStorage";

import enAbout from "@/locales/en/about.json";
import enAdmin from "@/locales/en/admin.json";
import enApprovals from "@/locales/en/approvals.json";
import enIntake from "@/locales/en/intake.json";
import enAuth from "@/locales/en/auth.json";
import enCommon from "@/locales/en/common.json";
import enDashboard from "@/locales/en/dashboard.json";
import enIntegrations from "@/locales/en/integrations.json";
import enInventory from "@/locales/en/inventory.json";
import enNotifications from "@/locales/en/notifications.json";
import enPolicies from "@/locales/en/policies.json";
import enProfile from "@/locales/en/profile.json";
import enProjectDetail from "@/locales/en/project_detail.json";
import enProjects from "@/locales/en/projects.json";
import enRemediation from "@/locales/en/remediation.json";
import enScans from "@/locales/en/scans.json";
import enSearch from "@/locales/en/search.json";
import koAbout from "@/locales/ko/about.json";
import koAdmin from "@/locales/ko/admin.json";
import koApprovals from "@/locales/ko/approvals.json";
import koIntake from "@/locales/ko/intake.json";
import koAuth from "@/locales/ko/auth.json";
import koCommon from "@/locales/ko/common.json";
import koDashboard from "@/locales/ko/dashboard.json";
import koIntegrations from "@/locales/ko/integrations.json";
import koInventory from "@/locales/ko/inventory.json";
import koNotifications from "@/locales/ko/notifications.json";
import koPolicies from "@/locales/ko/policies.json";
import koProfile from "@/locales/ko/profile.json";
import koProjectDetail from "@/locales/ko/project_detail.json";
import koProjects from "@/locales/ko/projects.json";
import koRemediation from "@/locales/ko/remediation.json";
import koScans from "@/locales/ko/scans.json";
import koSearch from "@/locales/ko/search.json";

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
      search: enSearch,
        admin: enAdmin,
        about: enAbout,
        approvals: enApprovals,
        intake: enIntake,
        dashboard: enDashboard,
        integrations: enIntegrations,
      inventory: enInventory,
        notifications: enNotifications,
        policies: enPolicies,
        profile: enProfile,
        remediation: enRemediation,
      },
      ko: {
        common: koCommon,
        auth: koAuth,
        projects: koProjects,
        project_detail: koProjectDetail,
        scans: koScans,
      search: koSearch,
        admin: koAdmin,
        about: koAbout,
        approvals: koApprovals,
        intake: koIntake,
        dashboard: koDashboard,
        integrations: koIntegrations,
      inventory: koInventory,
        notifications: koNotifications,
        policies: koPolicies,
        profile: koProfile,
        remediation: koRemediation,
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
    "search",
      "admin",
      "approvals",
      "intake",
      "dashboard",
      "integrations",
    "inventory",
      "notifications",
      "policies",
      "profile",
      "remediation",
    ],
    interpolation: { escapeValue: false },
    detection: {
      // First-time visitors default to ENGLISH, not their browser language:
      // the product's primary language is English (CLAUDE.md), and the public
      // demo is shown to a global audience. `navigator` is deliberately omitted
      // so a fresh visitor (no stored choice) falls through to `fallbackLng`
      // ("en") instead of auto-switching to e.g. a Korean browser locale. A
      // visitor who explicitly picks a language still has it remembered via
      // localStorage and restored on return.
      order: ["localStorage"],
      caches: ["localStorage"],
      lookupLocalStorage: LANGUAGE_STORAGE_KEY,
    },
  });

// Keep <html lang> in sync with the active language (a11y/SEO). i18next does
// not touch the document element, so without this the attribute stayed "en"
// even while the UI rendered Korean.
function syncHtmlLang(lng: string): void {
  if (typeof document !== "undefined") {
    document.documentElement.lang = lng.split("-")[0];
  }
}
syncHtmlLang(i18n.resolvedLanguage ?? "en");
i18n.on("languageChanged", syncHtmlLang);

export default i18n;
