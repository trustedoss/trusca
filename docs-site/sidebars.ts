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
      items: ["installation/docker-compose", "installation/upgrade"],
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
      ],
    },
    {
      type: "category",
      label: "Admin guide",
      collapsed: true,
      items: [
        "admin-guide/users-and-teams",
        "admin-guide/dt-connector",
        "admin-guide/disk-and-health",
        "admin-guide/audit-log",
        "admin-guide/backup-and-restore",
        "admin-guide/api-keys",
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
        "reference/architecture",
        "reference/env-variables",
        "reference/api-overview",
      ],
    },
  ],
};

export default sidebars;
