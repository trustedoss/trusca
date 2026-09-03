# Security Policy

The TRUSCA team takes the security of our software and our users seriously. This document describes how to report a vulnerability, what you can expect from us, and which versions we support.

> **Reminder:** TRUSCA is itself an SCA (Software Composition Analysis) tool. We hold ourselves to the same standard we help our users enforce.

---

## Reporting a Vulnerability

**Please do not open a public GitHub issue for an unpatched vulnerability.** Public disclosure before a fix is available puts users at risk.

### Preferred channel — GitHub Private Vulnerability Reporting

The fastest, most reliable channel is GitHub's built-in private vulnerability reporting:

→ **[Report a vulnerability](https://github.com/trustedoss/trusca/security/advisories/new)**

This creates a private security advisory visible only to you and the maintainers. Use this channel whenever possible.

### If you cannot use private reporting

GitHub's private vulnerability reporting is the only channel we operate. We do not run a security mailbox and we do not publish a PGP key, so there is no encrypted-email fallback to give you.

If private reporting is unavailable to you, open a regular issue that says only that you have found a security problem and how to reach you privately. Do not describe the vulnerability, and do not attach a proof of concept: the issue is public from the moment you file it. A maintainer will open a private advisory and invite you to it.

### What to include

A useful report includes:

1. **Affected version(s)** — release tag or Git SHA, deployment mode (Docker Compose / Helm / demo SaaS).
2. **Component** — backend / frontend / scan pipeline / Trivy integration / CI integration / install script / etc.
3. **Vulnerability class** — e.g. authentication bypass, IDOR, SSRF, SQL injection, XSS, RCE, secret exposure, supply-chain.
4. **Reproduction steps** — minimal and deterministic.
5. **Impact** — what an attacker can read, modify, or do.
6. **Proof of concept** — code, payload, or screenshots demonstrating the issue.
7. **Suggested mitigation** — optional, but appreciated.
8. **Your contact and disclosure preferences** — credit / anonymous, embargo timeline.

---

## Our Response — Service Level Agreement

| Stage | Target time | What happens |
|---|---|---|
| **Acknowledgement** | within **2 business days** | A maintainer confirms receipt and assigns a tracking ID. |
| **Triage & severity rating** | within **5 business days** | We confirm reproduction, assign CVSS v3.1 severity, and share an initial assessment. |
| **Fix development & remediation plan** | depends on severity (see below) | We share a remediation timeline. |
| **Patch release** | per severity targets | A patched release is published with release notes referencing the advisory. |
| **Public advisory** | after patch release + reasonable upgrade window | We publish a GitHub Security Advisory and request a CVE if applicable. |

### Remediation targets by severity

| Severity (CVSS v3.1) | Target remediation |
|---|---|
| **Critical** (9.0–10.0) | Patch within **7 days** of confirmed reproduction. |
| **High** (7.0–8.9) | Patch within **30 days**. |
| **Medium** (4.0–6.9) | Patch within **90 days**. |
| **Low** (0.1–3.9) | Patched in next regular release. |

These are targets, not guarantees. If we need more time (e.g., a complex fix touching the data model), we will communicate the revised timeline and the reason.

### Coordinated disclosure

Our default disclosure window is **90 days** from initial acknowledgement, or earlier if a patched release is available and a reasonable upgrade window has elapsed. Earlier disclosure may be appropriate when a vulnerability is already being exploited in the wild. We will coordinate the public advisory date with the reporter.

---

## Recognition

We maintain a public **Security Hall of Thanks** in our advisories and release notes for reporters who follow responsible disclosure. If you prefer to remain anonymous, just let us know in your report.

We do not currently run a paid bug bounty program. We may offer swag for high-quality reports at our discretion.

---

## Supported Versions

TRUSCA is pre-1.0. Only the newest release receives security patches.

| Version | Support status |
|---|---|
| Latest `0.x` patch release | Supported. Security fixes ship as a new patch release. |
| Any earlier `0.x` release | Not supported. Upgrade to the latest patch. |
| `main` branch | Best effort. Not a supported deployment target. |

Because the project is pre-1.0, a minor release may change the HTTP API, the configuration keys, or the database schema. A longer support window covering the previous minor as well will be defined at 1.0.0.

---

## Scope

This policy covers vulnerabilities in:

- TRUSCA backend (`apps/backend/`)
- TRUSCA frontend (`apps/frontend/`)
- Bundled integrations and Celery tasks (`apps/backend/integrations/`, `apps/backend/tasks/`)
- Official Docker images, Docker Compose configurations, and Helm chart
- Official install / upgrade / backup / restore scripts (`scripts/`)
- The public demo deployment (`trusca-demo.duckdns.org`)
- The official GitHub Action / GitLab CI template / Jenkinsfile examples

### Out of scope

- Vulnerabilities in upstream third-party software (cdxgen, Trivy, PostgreSQL, Redis, etc.). Please report those to the respective projects. We will coordinate downstream patches once an upstream advisory is available.
- Findings from automated scanners that have no demonstrated impact (e.g. "missing X-Frame-Options on a non-rendering API").
- Social-engineering scenarios that require maintainers to take an unusual action.
- Denial-of-service via volumetric load against the demo SaaS instance.

---

## Hardening Guidance

If you operate a TRUSCA deployment, we recommend reviewing:

- Enabling HTTPS at the edge (Traefik configuration is included in the production compose file).
- Rotating the `SECRET_KEY` and the database passwords on installation.
- Restricting the production CORS allowlist to your portal domain only.
- Setting `DISK_HARD_LIMIT_PCT` so scans abort before disk exhaustion.
- Subscribing to release notifications on the GitHub repo so you are alerted when patches ship.

A dedicated hardening guide is on the [roadmap](ROADMAP.md). Until it ships, the [admin guide](https://trustedoss.github.io/trusca/docs/admin-guide/oncall-runbook) and the [environment variable reference](https://trustedoss.github.io/trusca/docs/reference/env-variables) cover the settings above.

---

## Cryptographic Verification

Every release attaches a CycloneDX SBOM of the source tree to its GitHub Release.

Release tags are not GPG-signed and container images are not cosign-signed today. Signed tags, signed images, and a provenance attestation are on the [roadmap](ROADMAP.md); when they ship, verification instructions go in the release notes and this section is updated. Until then, verify a release by its commit SHA and by the image digest the release workflow prints.

---

## Contact

| Topic | Channel |
|---|---|
| **Vulnerability report** | [GitHub Private Vulnerability Reporting](https://github.com/trustedoss/trusca/security/advisories/new) |
| Security policy questions | [GitHub Issues](https://github.com/trustedoss/trusca/issues) (the policy itself is public; only reports are private) |
| Conduct concerns | A private report to the maintainers, see [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) |
| Everything else | [GitHub Issues](https://github.com/trustedoss/trusca/issues) |

Thank you for helping keep TRUSCA and its users safe.
