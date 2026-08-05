# Roadmap

Public roadmap for TRUSCA. Intentionally high-level — concrete
priorities and target dates are decided per release cycle and announced in
[`CHANGELOG.md`](CHANGELOG.md).

Priorities follow three principles, in order:

1. **Fix what blocks adoption** — public-facing accuracy and trust.
2. **Reach parity** with running a single SCA tool directly (don't be a thinner wrapper).
3. **Differentiate** with capabilities a single tool doesn't give you.

Legend: ☐ planned · ◐ in progress

---

## Recently shipped (v0.11.0 – v0.20.1)

Highlights only, and only what closes a roadmap line — the itemised history,
including the v0.10.0 foundation (Trivy as the single matching engine, VEX,
EPSS, the Helm chart, the design system), is in [`CHANGELOG.md`](CHANGELOG.md).

- **Known-malicious package detection** — OSV `MAL-` advisories as a build-gate axis, with expiry-capped exceptions and a weekly re-check that catches a package flagged after it shipped
- **Automated remediation pull requests**, with a dry run, on top of the minimum-safe-upgrade engine and a "group by upgrade" view of the findings one bump resolves
- **Dynamic license policy** — per-team and per-organization category overrides, exceptions, and gate posture
- **Signed SBOMs** — cosign signatures plus an in-toto / SLSA provenance attestation on every source scan, verifiable outside the portal
- **SBOM conformance scoring** for uploaded documents, with a regulatory crosswalk (BSI TR-03183-2 for the EU CRA, the NTIA minimum elements, EU AI Act Annex IV, the Korean AI Framework Act) and CycloneDX 1.7 ML-BOM support
- **CISA KEV** badges and remediation due dates, with a KEV-first Priority sort
- **Vulnerability SLA / aging** — a first-detection clock that survives re-scans, severity-based due dates, and a daily breach sweep
- **End-of-life components**, a "behind latest patch" signal, and base-image OS end-of-service-life on container scans
- **Organization-wide component inventory and search** — which projects carry a package, and which projects a CVE reaches
- **Release labels as permanent snapshot addresses** (`?release=`) across the detail endpoints, SBOM, and NOTICE
- **Dark theme** alongside light, both measured against WCAG AA

## Supply-chain Integrity

Align with CISA 2025 / SLSA, cut noise further.

- ☐ **Reachability**-based prioritization (best-effort, rolled out per language)
- ☐ **Declared-vs-actual SBOM drift** — diff an uploaded SBOM against TRUSCA's own scan of the same ref and report what each side is missing. The conformance score grades whether the document's fields are filled in; this answers whether the document matches the code.

## Threat Detection & Deeper Prioritization

Close the biggest remaining gaps vs commercial SCA, reusing best-of-breed open source rather than building from scratch.

- ☐ **Typosquat heuristics** — name-distance and popularity signals alongside the OSV `MAL-` feed, which only names packages an advisory has already caught up with
- ☐ **Unified risk score** — one 0–100 number combining CVSS, EPSS, KEV, fix availability, and dependency depth. The current score reads severity and license distribution only; the other signals are surfaced but not folded in.
- ☐ **Binary scanning** (OSS-in-binary) — Syft binary classifier + Trivy filesystem mode (best-effort; no modified-binary fingerprinting)
- ☐ **AI-BOM generation** — detect AI model / dataset components and their licenses at scan time via cdxgen. Uploaded ML-BOMs are already ingested and scored; producing one is not.
- ☐ **Snippet / AI-generated-code origin matching** — *lowest priority, RFC-gated.* ScanOSS (MIT client + GPL-2.0 engine as an isolated sidecar). Requires a separate RFC on knowledge-base hosting and fingerprint egress before any work starts.

---

## Explicitly out of scope

- **Building our own vulnerability database** — we aggregate through Trivy's unified DB (NVD + OSV + GHSA + EPSS + KEV) and augment with VEX.

## Backlog (not yet scheduled)

SSO / OIDC, native Jenkins plugin, per-project / per-scan exclude paths (ignore generated / test / vendored trees in first-party license detection), a daily rather than weekly malicious re-check (the re-stamp half needs no network, and a package can be flagged hours after it ships).

---

Roadmap items are proposals, not commitments — dates are deliberately omitted. Feedback and contributions are welcome: open a [discussion](https://github.com/trustedoss/trusca/discussions) or an issue referencing the relevant section.
