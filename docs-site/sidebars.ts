// Sidebar definition. Order here drives the order in the rendered sidebar
// for both locales — KO mirrors translate the labels but keep the structure.

import type { SidebarsConfig } from "@docusaurus/plugin-content-docs";

const sidebars: SidebarsConfig = {
  docs: [
    "intro",
    {
      type: "category",
      label: "Installation",
      collapsed: false,
      items: [
        "installation/docker-compose",
        "installation/upgrade",
        "installation/helm",
        "installation/gcp-deploy",
        "installation/live-demo",
        "installation/uat-checklist",
      ],
    },
    {
      type: "category",
      label: "User guide",
      collapsed: false,
      items: [
        "user-guide/projects",
        "user-guide/scans",
        "user-guide/components-and-licenses",
        "user-guide/vulnerabilities",
        "user-guide/sbom",
        "user-guide/approvals",
        "user-guide/auth-and-profile",
        "user-guide/notifications",
        "user-guide/integrations",
      ],
    },
    {
      type: "category",
      label: "Contributor guide",
      collapsed: true,
      items: [
        "contributor-guide/getting-started",
        "contributor-guide/coding-standards",
        "contributor-guide/testing-guide",
        "contributor-guide/agent-team",
      ],
    },
    {
      type: "category",
      label: "Admin guide",
      collapsed: false,
      items: [
        "admin-guide/users-and-teams",
        "admin-guide/dt-connector",
        "admin-guide/disk-and-health",
        "admin-guide/audit-log",
        "admin-guide/oncall-runbook",
        "admin-guide/backup-and-restore",
        "admin-guide/api-keys",
        "admin-guide/github-app",
      ],
    },
    {
      type: "category",
      label: "CI integration",
      collapsed: true,
      items: [
        "ci-integration/github-actions",
        "ci-integration/gitlab-ci",
        "ci-integration/jenkins",
        "ci-integration/webhooks",
      ],
    },
    {
      type: "category",
      label: "Reference",
      collapsed: true,
      items: [
        "comparison",
        "reference/architecture",
        "reference/glossary",
        "reference/env-variables",
        "reference/api-overview",
        "reference/license-policies",
      ],
    },
    {
      type: "category",
      label: "Release notes",
      collapsed: true,
      items: ["release-notes/v2-0-0"],
    },
  ],
};

export default sidebars;
