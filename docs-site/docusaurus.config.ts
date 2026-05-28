// TrustedOSS Portal — Docusaurus configuration.
//
// Two locales (en, ko) ship together at GA per CLAUDE.md. The site is built
// on every push to `main` by .github/workflows/docs.yml and deployed to
// GitHub Pages at https://trustedoss.github.io/trustedoss-portal/.

import { themes as prismThemes } from "prism-react-renderer";
import type { Config } from "@docusaurus/types";
import type * as Preset from "@docusaurus/preset-classic";

const config: Config = {
  title: "TrustedOSS Portal",
  tagline: "Enterprise OSS Risk Management — Apache-2.0",
  favicon: "img/favicon.svg",

  url: "https://trustedoss.github.io",
  baseUrl: "/trustedoss-portal/",

  organizationName: "trustedoss",
  projectName: "trustedoss-portal",
  deploymentBranch: "gh-pages",
  trailingSlash: false,

  onBrokenLinks: "warn",
  onBrokenMarkdownLinks: "warn",

  markdown: {
    mermaid: true,
  },

  i18n: {
    defaultLocale: "en",
    locales: ["en", "ko"],
    localeConfigs: {
      en: { label: "English", direction: "ltr", htmlLang: "en-US" },
      ko: { label: "한국어", direction: "ltr", htmlLang: "ko-KR" },
    },
  },

  presets: [
    [
      "classic",
      {
        docs: {
          sidebarPath: "./sidebars.ts",
          // Source of truth lives in this monorepo. Doc edits are encouraged
          // via PR — the "Edit this page" link below targets the right path.
          editUrl:
            "https://github.com/trustedoss/trustedoss-portal/edit/main/docs-site/",
          editLocalizedFiles: true,
          showLastUpdateAuthor: false,
          showLastUpdateTime: true,
        },
        blog: false,
        theme: {
          customCss: "./src/css/custom.css",
        },
      } satisfies Preset.Options,
    ],
    // v2.1 Track B (B4): API reference hosting via redocusaurus (Redoc).
    // The spec is a STATIC, committed snapshot at static/openapi.json,
    // regenerated from FastAPI's app.openapi() by scripts/dump_openapi.py and
    // guarded against drift in CI (ci.yml backend test job). The docs build
    // therefore needs no running backend. Rendered at /reference/api so it sits
    // next to the hand-written API overview. The same static route is emitted
    // for both locales; the spec itself is language-neutral (HTTP contract).
    [
      "redocusaurus",
      {
        specs: [
          {
            id: "trustedoss-api",
            spec: "static/openapi.json",
            route: "/reference/api",
          },
        ],
        theme: {
          // Match the site's primary (dark navy, Black Duck style) so the
          // Redoc panel does not look bolted on.
          primaryColor: "#0f172a",
        },
      },
    ],
  ],

  themeConfig: {
    image: "img/social-card.png",
    colorMode: {
      defaultMode: "light",
      disableSwitch: false,
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: "TrustedOSS Portal",
      logo: {
        alt: "TrustedOSS Portal Logo",
        src: "img/logo.svg",
      },
      items: [
        {
          type: "docSidebar",
          sidebarId: "docs",
          position: "left",
          label: "Docs",
        },
        {
          to: "/docs/installation/docker-compose",
          label: "Install",
          position: "left",
        },
        {
          to: "/docs/admin-guide/users-and-teams",
          label: "Admin",
          position: "left",
        },
        {
          to: "/docs/ci-integration/github-actions",
          label: "CI",
          position: "left",
        },
        {
          to: "/docs/reference/architecture",
          label: "Reference",
          position: "left",
        },
        {
          to: "/reference/api",
          label: "API reference",
          position: "left",
        },
        { type: "localeDropdown", position: "right" },
        {
          href: "https://github.com/trustedoss/trustedoss-portal",
          label: "GitHub",
          position: "right",
        },
      ],
    },
    footer: {
      style: "dark",
      links: [
        {
          title: "Docs",
          items: [
            { label: "Introduction", to: "/docs/intro" },
            { label: "Install", to: "/docs/installation/docker-compose" },
            { label: "Admin guide", to: "/docs/admin-guide/users-and-teams" },
            { label: "CI integration", to: "/docs/ci-integration/github-actions" },
          ],
        },
        {
          title: "Project",
          items: [
            {
              label: "GitHub",
              href: "https://github.com/trustedoss/trustedoss-portal",
            },
            {
              label: "Issues",
              href: "https://github.com/trustedoss/trustedoss-portal/issues",
            },
            {
              label: "Releases",
              href: "https://github.com/trustedoss/trustedoss-portal/releases",
            },
          ],
        },
        {
          title: "Reference",
          items: [
            { label: "Architecture", to: "/docs/reference/architecture" },
            { label: "Environment variables", to: "/docs/reference/env-variables" },
            { label: "API overview", to: "/docs/reference/api-overview" },
            { label: "API reference (Redoc)", to: "/reference/api" },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} TrustedOSS — Licensed under Apache-2.0.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: [
        "bash",
        "yaml",
        "json",
        "toml",
        "python",
        "typescript",
        "tsx",
        "groovy",
        "docker",
      ],
    },
  } satisfies Preset.ThemeConfig,

  // Offline lunr-style search. Ships an index per locale; KO tokenizer is
  // bundled. Cmd+K / Ctrl+K opens the search bar by default. No external service.
  themes: [
    [
      "@easyops-cn/docusaurus-search-local",
      {
        hashed: true,
        language: ["en", "ko"],
        indexBlog: false,
        indexPages: false,
        docsRouteBasePath: "/docs",
        highlightSearchTermsOnTargetPage: true,
        searchResultLimits: 8,
      },
    ],
    "@docusaurus/theme-mermaid",
  ],
};

export default config;
