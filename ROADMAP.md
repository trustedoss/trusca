# Roadmap

Public roadmap for TRUSCA. Intentionally high-level — concrete
priorities and target dates are decided per release cycle and announced in
[`CHANGELOG.md`](CHANGELOG.md).

Priorities follow three principles, in order:

1. **Fix what blocks adoption** — public-facing accuracy and trust.
2. **Reach parity** with running a single SCA tool directly (don't be a thinner wrapper).
3. **Differentiate** with capabilities a single tool doesn't give you.

Legend: ☐ planned · ◐ in progress · ☑ done in the current release

---

## Recently shipped (in v0.10.0)

- Trivy as the single vulnerability matching engine — unified NVD + OSV + GHSA + EPSS + KEV DB, weekly refresh, air-gapped support, automatic CVE re-detection
- EPSS prioritization as a first-class signal (column / sort / filter / policy-gate threshold)
- VEX consumption and export (OpenVEX + CycloneDX VEX)
- Per-finding `fixed_version` + direct vs. transitive dependency depth
- Production-grade Helm chart with Ingress + cert-manager TLS + migration Job
- Read-only demo mode (`DEMO_READ_ONLY=true`)
- Modern enterprise design system (light theme, WCAG AA, compact tables, dual drawer/page surfaces)
- Filter URL persistence + global ⌘K palette + Portfolio Dashboard
- EN + KO i18n for every UI string and every documentation page
- GitHub Actions composite action + GitLab CI template + Jenkinsfile example
- Ref-keyed scan retention (latest scan + findings per project ref, manual delete)
- Time-boxed forbidden-license waivers (`LICENSE_WAIVE_MAX_DAYS` cap)
- Collapsible sidebar + responsive mobile drawer
- Documentation UAT harness + re-enabled SAST / e2e / supply-chain CI gates

## Remediation & Policy

Close the "detect → act" loop and remove the static-policy limitation.

- ◐ **Suggested dependency upgrades** — compute the minimal safe bump from `fixed_version` + dependency graph
- ☐ **Automated upgrade PRs** (opt-in, per-ecosystem, dry-run first)
- ☐ **Dynamic license policy engine** — per-team/org editable rules

## Supply-chain Integrity

Align with CISA 2025 / SLSA, cut noise further.

- ☐ **Signed SBOMs** — cosign signatures, in-toto attestation, SLSA provenance, CISA 2025 / NTIA element coverage
- ☐ **Reachability**-based prioritization (best-effort, rolled out per language)

## Threat Detection & Deeper Prioritization

Close the biggest remaining gaps vs commercial SCA, reusing best-of-breed open source rather than building from scratch.

- ☐ **Malicious / typosquatting package detection** — OSSF malicious-packages + OSV `MAL-` feeds plus typosquat heuristics; a new `malicious` finding type that blocks the build gate (parity with Snyk / Sonatype Repository Firewall)
- ☐ **CISA KEV + unified Risk Score** — a known-exploited flag plus a single 0–100 score combining CVSS, EPSS, KEV, fix availability, and dependency depth
- ☐ **Binary scanning** (OSS-in-binary) — Syft binary classifier + Trivy filesystem mode (best-effort; no modified-binary fingerprinting)
- ☐ **AI-BOM** — CycloneDX ML-BOM via cdxgen: detect AI model / dataset components and their licenses
- ☐ **Snippet / AI-generated-code origin matching** — *lowest priority, RFC-gated.* ScanOSS (MIT client + GPL-2.0 engine as an isolated sidecar). Requires a separate RFC on knowledge-base hosting and fingerprint egress before any work starts.

---

## Explicitly out of scope

- **Building our own vulnerability database** — we aggregate through Trivy's unified DB (NVD + OSV + GHSA + EPSS + KEV) and augment with VEX.

## Backlog (not yet scheduled)

SSO / OIDC, native Jenkins plugin, Excel reports, compliance PDF, historical-scan pinning on SBOM/NOTICE, dark mode, per-project / per-scan exclude paths (ignore generated / test / vendored trees in first-party license detection).

---

Roadmap items are proposals, not commitments — dates are deliberately omitted. Feedback and contributions are welcome: open a [discussion](https://github.com/trustedoss/trusca/discussions) or an issue referencing the relevant section.
