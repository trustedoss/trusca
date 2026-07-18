---
id: glossary
title: Glossary
description: TRUSCA domain terms — SCA, SBOM, VEX, license tiers, RBAC roles, and CycloneDX/SPDX mappings.
sidebar_label: Glossary
sidebar_position: 4
---

# Glossary

Single source of truth for the domain terms used across this site.
Each entry pairs the **full name**, the **abbreviation** (when one is
used), and the **canonical reference link** to the relevant spec or
upstream project.

:::note Audience
Anyone reading the rest of this site. Skim once on first visit;
keep open in a tab while reading the user, admin, or contributor
guides.
:::

## SCA core

- **SCA — Software Composition Analysis.** The discipline of detecting
  third-party (open-source) components in a software project,
  classifying their licenses, and identifying their known
  vulnerabilities. TRUSCA is an SCA tool.
- **SBOM — Software Bill of Materials.** A machine-readable inventory
  of every component (and its version, license, and supplier) that
  ships with a piece of software. TRUSCA exports SBOMs in
  CycloneDX (JSON / XML) and SPDX (JSON / Tag-Value) formats. See
  [CISA SBOM resources](https://www.cisa.gov/sbom).
- **CycloneDX.** OWASP-maintained SBOM specification. TRUSCA uses
  version 1.6 (JSON + XML). See
  [cyclonedx.org/specification](https://cyclonedx.org/specification/).
- **SPDX — Software Package Data Exchange.** Linux Foundation-maintained
  SBOM specification. TRUSCA uses version 2.3 (JSON + Tag-Value).
  See [spdx.dev](https://spdx.dev/).

## Vulnerabilities

- **CVE — Common Vulnerabilities and Exposures.** The
  industry-standard identifier for a publicly disclosed security flaw,
  formatted as `CVE-YYYY-NNNN`. Maintained by MITRE.
  See [cve.org](https://www.cve.org/).
- **CWE — Common Weakness Enumeration.** A taxonomy of software weakness
  types (e.g. CWE-79 Cross-site Scripting). Each CVE often references
  one or more CWE entries.
- **NVD — National Vulnerability Database.** NIST's analysis layer on
  top of CVE — adds CVSS scores, CPE matching, references. See
  [nvd.nist.gov](https://nvd.nist.gov/).
- **CVSS — Common Vulnerability Scoring System.** A 0–10 score for the
  theoretical **severity** (impact) of a CVE. Says nothing about whether
  it is being exploited.
- **EPSS — Exploit Prediction Scoring System.** A 0–1 probability that a
  CVE will be **exploited in the wild** within the next 30 days. EPSS
  complements CVSS: CVSS is severity, EPSS is likelihood. TRUSCA
  shows the score as a percentage and the percentile as "top N%", and
  can drive the build gate via `GATE_EPSS_THRESHOLD`. EPSS is collected
  from the Trivy DB and is absent for CVEs Trivy does not score. See
  [first.org/epss](https://www.first.org/epss/) and the
  [EPSS user guide](../user-guide/vulnerabilities.md#epss--exploitation-probability).
- **OSV — Open Source Vulnerabilities database.** Google-led, ecosystem-
  scoped vulnerability database (npm, PyPI, Maven, etc.). See
  [osv.dev](https://osv.dev/).
- **GHSA — GitHub Security Advisory.** GitHub's per-ecosystem advisory
  feed. CVE IDs are often issued via GHSA.
- **VEX — Vulnerability Exploitability eXchange.** A document format for
  asserting whether a known vulnerability actually affects a given
  product. CycloneDX `analysis.state` and SPDX VEX are the two main
  encodings. TRUSCA implements the 7-state CycloneDX model:
  `new`, `analyzing`, `exploitable`, `not_affected`, `false_positive`,
  `suppressed`, `fixed`. See
  [CycloneDX VEX](https://cyclonedx.org/capabilities/vex/).

### VEX 7-state — action buttons per state

The vulnerability drawer's Analysis section shows up to seven action
buttons depending on the current state. The mapping is:

| Current state | Available actions (button labels) |
|---------------|-----------------------------------|
| `new` | Move to analyzing, Mark exploitable, Mark not affected, Mark false positive, Suppress, Mark fixed |
| `analyzing` | Mark exploitable, Mark not affected, Mark false positive, Suppress, Mark fixed |
| `exploitable` | Mark not affected, Mark false positive, Mark fixed |
| `not_affected` | Reopen as new, Mark exploitable, Mark fixed |
| `false_positive` | Reopen as new, Mark exploitable |
| `suppressed` | Reopen as new |
| `fixed` | Reopen as new |

Each button writes a `vulnerability_findings.update` row to `audit_logs`
with the `previous_status` → `new_status` transition in the `diff`
column.

## Tools

- **scancode — scancode-toolkit.** License scanner that reads a
  project's **first-party** source files directly and emits *detected*
  SPDX licenses, each tagged with the `source_path` of the file it was
  found in. TRUSCA runs scancode as the second source-scan stage
  (it replaced the OSS Review Toolkit, ORT, in this release). Third-party
  dependency sources are not scanned — their licenses stay *declared*
  (from cdxgen). See
  [github.com/aboutcode-org/scancode-toolkit](https://github.com/aboutcode-org/scancode-toolkit).
- **cdxgen — CycloneDX Generator.** Component detector that produces
  CycloneDX SBOMs from 30+ language / build-system manifests
  (`package.json`, `pom.xml`, `requirements.txt`, …). Runs as the
  first scan stage, before scancode.
- **Trivy.** Container and OS-package vulnerability scanner from
  Aqua Security. TRUSCA uses Trivy for the container-scan
  pipeline (separate from the cdxgen + scancode source-scan path).
- **Trivy DB.** Compiled bundle of NVD + OSV + GHSA + EPSS + KEV
  published by Aqua Security at `ghcr.io/aquasecurity/trivy-db`.
  TRUSCA downloads it once at worker boot and refreshes it weekly
  (`TRIVY_DB_REPOSITORY`, `TRIVY_DB_REFRESH_HOURS`). See
  [Vulnerability data (Trivy DB)](../admin-guide/vulnerability-data.md) and
  [Data sources](./data-sources.md).
- **DT — Dependency-Track.** Apache-2.0 vulnerability intelligence platform.
  TRUSCA used DT as its vulnerability engine through  and replaced it
  with Trivy at v0.10.0 — see
  [ADR-0001](https://github.com/trustedoss/trusca/blob/main/docs/decisions/0001-replace-dt-with-trivy.md)
  and [Comparison](../comparison.md#vs-dependency-track). The DT term still
  appears in this glossary because legacy audit-log rows and the comparison
  page reference it.
- **cosign.** Sigstore's signing CLI. TRUSCA signs every source
  scan's CycloneDX SBOM with cosign (`cosign sign-blob`) so a consumer
  can verify it with `cosign verify-blob`. Key-based signing is the
  self-hosted default; keyless (OIDC) is opt-in. See
  [Verify SBOM signatures](../ci-integration/sbom-signature-verification.md) and
  [docs.sigstore.dev/cosign](https://docs.sigstore.dev/cosign/overview/).
- **Sigstore / Fulcio / Rekor.** The keyless-signing ecosystem cosign
  draws on: **Fulcio** issues a short-lived signing certificate bound to
  an OIDC identity, and **Rekor** is the public transparency log the
  signature is recorded in. Only used when `COSIGN_KEYLESS=true`. See
  [sigstore.dev](https://www.sigstore.dev/).
- **Attestation / provenance (in-toto, SLSA).** A signed statement about
  *how* an artifact was produced. TRUSCA emits an
  [in-toto](https://in-toto.io/) Statement carrying
  [SLSA](https://slsa.dev/) provenance (builder identity + build
  context) alongside the SBOM signature. See
  [Verify SBOM signatures](../ci-integration/sbom-signature-verification.md#inspect-the-provenance-attestation).

## License classification

The portal classifies licenses into four **tiers**:

| Tier (code value) | UI label | Build-gate effect |
|-------------------|----------|-------------------|
| `forbidden` | Forbidden | Build fails — CI exit code 1 |
| `conditional` | Conditional | Requires component approval; warning until approved |
| `permissive` | Allowed | No restriction |
| `unknown` | Unknown | Surfaced for review; no automatic block |

The classification is driven by the
`_LICENSE_CATEGORY_DEFAULTS` dict in
`apps/backend/tasks/scan_source.py` (operator-side override path;
ORT-driven per-org rules are on the roadmap). The values
`forbidden` / `conditional` / `permissive` / `unknown` appear in API
responses, audit logs, and policy gate verdicts; the UI labels
`Forbidden` / `Conditional` / `Allowed` / `Unknown` appear in tables
and badges. See
[Components & licenses](../user-guide/components-and-licenses.md#license-classification).

## Build gates

The portal exposes one CI-blocking mechanism, called the **build gate**
(also referred to as the **policy gate** in some operator-facing
contexts — they are the same thing). The gate evaluates:

1. Are there any CVEs at or above the project's severity floor (default
   `Critical`; per-project `policy_gate.severity_floor` is
   configurable)?
2. Are there any components in the `forbidden` license tier?
3. *(Optional)* Are there any open findings whose **EPSS** score meets
   or exceeds `GATE_EPSS_THRESHOLD`? This third condition is **disabled
   by default** — it activates only when an operator sets the
   `GATE_EPSS_THRESHOLD` env var (a value from `0` to `1`). When unset,
   the gate evaluates conditions 1 and 2 exactly as before. See
   [EPSS](../user-guide/vulnerabilities.md#epss--exploitation-probability).

Any active condition triggers exit code 1 in the CI integration's
composite action. A failed gate is recorded in `audit_logs` with the
list of offending CVEs / licenses; when the EPSS condition is enabled,
the gate result also carries `epss_gate_count` and `epss_threshold`.

## RBAC roles

- **`super_admin`** — system-wide. Manages users, teams, vulnerability
  data (Trivy DB), scan queue, disk, audit. Created by the install wizard or
  the `create_super_admin.py` script.
- **`team_admin`** — bounded to a single team. Manages team settings,
  team members, and project visibility within the team.
- **`developer`** — bounded to a team's project set. Runs scans, views
  results, reviews approvals.

A single user may hold a different role in each team they belong to
(e.g. `team_admin` in team A and `developer` in team B); the
Memberships drawer in `/admin/users/<id>` shows all assignments.

## API key scopes

API keys carry a single **scope**:

| Scope | Issued by | Effect |
|-------|-----------|--------|
| `org` | super-admin only | Authenticates against any endpoint in the org |
| `team` | super-admin, team-admin | Bounded to one team's projects |
| `project` | super-admin, team-admin, developer (within their team's projects) | Bounded to one project |

There is no per-action allowlist in this release; any caller authenticated
with a key in the right scope can hit any endpoint that accepts an
API key. Per-action capabilities are on the roadmap.

## Operational terminology

- **Circuit breaker (CLOSED / OPEN / HALF_OPEN).** A failure-domain
  isolation pattern. TRUSCA used a breaker to wrap the Dependency-Track
  API client through ; with the Trivy DB now local to the worker
  (v0.10.0+), the pattern is no longer used in the vulnerability path. The
  general term still appears in operator literature.
- **`audit_logs`.** Append-only table capturing every state-changing
  operation (CRUD on first-class entities, plus explicit business
  events). See [Audit log](../admin-guide/audit-log.md).
- **Workspace.** Per-scan checkout directory under
  `/opt/trustedoss/workspace` (host) / `/workspace` (container).
  Cleaned up by the disk-pressure subsystem (> 30 days idle).

## See also

- [Architecture](./architecture.md) — how the pieces fit together
- [API overview](./api-overview.md) — REST + WebSocket surface
- [Environment variables](./env-variables.md) — every config knob
