# Security Policy

The TRUSCA team takes the security of our software and our users seriously. This document describes how to report a vulnerability, what you can expect from us, and which versions we support.

> **Reminder:** TRUSCA is itself an SCA (Software Composition Analysis) tool. We hold ourselves to the same standard we help our users enforce.

---

## Reporting a Vulnerability

**Please do not open a public GitHub issue for an unpatched vulnerability.** Public disclosure before a fix is available puts users at risk.

### Preferred channel — GitHub Private Vulnerability Reporting

The fastest, most reliable channel is GitHub's built-in private vulnerability reporting:

→ **[Report a vulnerability](https://github.com/trustedoss/trustedoss-portal/security/advisories/new)**

This creates a private security advisory visible only to you and the maintainers. Use this channel whenever possible.

### Alternative — Encrypted email

If you cannot use GitHub's private reporting, send an encrypted email to:

- **Address:** `security@trustedoss.io`
- **PGP key fingerprint:** `0000 0000 0000 0000 0000  0000 0000 0000 0000 0000` *(placeholder — to be replaced before v2.0.0 GA)*
- **PGP public key:** published at [https://trustedoss.io/.well-known/pgp-key.asc](https://trustedoss.io/.well-known/pgp-key.asc) *(placeholder)*

If encryption is genuinely impossible, plain email is acceptable but strongly discouraged. Avoid pasting full proof-of-concept payloads in plain text.

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

| Version | Support status |
|---|---|
| Pre-release / `main` branch | Best effort during the v2 development phase. |
| `v2.x` | Will be defined at v2.0.0 GA. We expect to support the latest minor for security patches and the previous minor for at least 6 months. |

Older versions (including v1) do not receive security patches. Please upgrade.

---

## Scope

This policy covers vulnerabilities in:

- TRUSCA backend (`apps/backend/`)
- TRUSCA frontend (`apps/frontend/`)
- Bundled integrations and Celery tasks (`apps/backend/integrations/`, `apps/backend/tasks/`)
- Official Docker images, Docker Compose configurations, and Helm chart
- Official install / upgrade / backup / restore scripts (`scripts/`)
- Demo SaaS deployment (`demo.trustedoss.io`)
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
- Rotating the `SECRET_KEY`, `DT_API_KEY`, and database password on installation.
- Restricting the production CORS allowlist to your portal domain only.
- Setting `DISK_HARD_LIMIT_PCT` so scans abort before disk exhaustion.
- Subscribing to release notifications on the GitHub repo so you are alerted when patches ship.

A more detailed hardening guide will ship with the v2.0.0 GA documentation.

---

## Cryptographic Verification

Starting at **v2.0.0 GA**, every release tag will be:

- Signed by the maintainer release key (Sigstore / cosign for container images, GPG for tags).
- Accompanied by an SBOM (CycloneDX JSON) and SLSA provenance attestation.

Verification instructions will be published in the release notes.

---

## Contact

| Topic | Channel |
|---|---|
| **Vulnerability report** | [GitHub Private Vulnerability Reporting](https://github.com/trustedoss/trustedoss-portal/security/advisories/new) → fall back to `security@trustedoss.io` |
| Security policy questions | `security@trustedoss.io` (no encryption needed) |
| Conduct concerns | `conduct@trustedoss.io` (see [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)) |
| Everything else | GitHub Issues / Discussions |

Thank you for helping keep TRUSCA and its users safe.
