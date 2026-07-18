// Sidebar definition. Order here drives the order in the rendered sidebar
// for both locales — KO mirrors translate the labels but keep the structure.

import type { SidebarsConfig } from "@docusaurus/plugin-content-docs";

const sidebars: SidebarsConfig = {
  docs: [
    "intro",
    "quickstart",
    "comparison",
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
        "user-guide/dashboard",
        "user-guide/projects",
        "user-guide/scans",
        "user-guide/components-and-licenses",
        "user-guide/vulnerabilities",
        "user-guide/triage",
        "user-guide/sbom",
        "user-guide/ai-sbom-conformance",
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
        "contributor-guide/releasing",
      ],
    },
    {
      type: "category",
      label: "Admin guide",
      collapsed: false,
      items: [
        "admin-guide/users-and-teams",
        "admin-guide/vulnerability-data",
        "admin-guide/disk-and-health",
        "admin-guide/scan-retention",
        "admin-guide/dynamic-scan-executor",
        "admin-guide/audit-log",
        "admin-guide/oncall-runbook",
        "admin-guide/backup-and-restore",
        "admin-guide/api-keys",
        "admin-guide/github-app",
      ],
    },
    {
      type: "category",
      label: "Best practices",
      collapsed: true,
      items: [
        "best-practices/scan-frequency",
        "best-practices/policy-design",
        "best-practices/team-structure",
        "best-practices/upgrade-cadence",
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
        "ci-integration/sbom-upload",
      ],
    },
    {
      type: "category",
      label: "Reference",
      collapsed: true,
      items: [
        "reference/architecture",
        "reference/glossary",
        "reference/faq",
        "reference/env-variables",
        "reference/api-overview",
        "reference/data-sources",
        "reference/analysis-types",
        "reference/license-policies",
        "reference/obligation-catalog",
        "reference/remediation-dry-run",
        "reference/remediation-pull-request",
        "reference/sbom-signature-verification",
        "reference/design-system",
      ],
    },
    {
      type: "category",
      label: "Release notes",
      collapsed: true,
      items: [
        "release-notes/v0-14-0",
        "release-notes/v0-13-1",
        "release-notes/v0-13-0",
        "release-notes/v0-12-0",
        "release-notes/v0-11-1",
        "release-notes/v0-11-0",
        "release-notes/v0-10-0",
      ],
    },
  ],
};

export default sidebars;
