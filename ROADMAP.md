# Roadmap

This is the public roadmap for TrustedOSS Portal after the **v2.0.0** general-availability
release. It is intentionally high-level; the detailed, PR-level execution plan lives in
[`docs/post-ga-roadmap.md`](docs/post-ga-roadmap.md).

Priorities follow three principles, in order:

1. **Fix what blocks adoption** — public-facing accuracy and trust.
2. **Reach parity** with running Dependency-Track directly (don't be a thinner wrapper).
3. **Differentiate** with capabilities a single tool doesn't give you.

Legend: ☐ planned · ◐ in progress · ☑ done

---

## v2.0.1 — Docs, Web & Distribution Hardening

Make the public first impression accurate **and make the install actually work**. No product
code changes, but it does include release/CI work — images that aren't published can't be installed.

- ☑ **Publish container images** — `release.yml` builds & pushes multi-arch (amd64+arm64)
  `backend`/`worker`/`frontend` to ghcr.io on tag; compose namespace aligned to `ghcr.io/trustedoss`
  *(takes effect once the first `vX.Y.Z` tag is cut and the org grants packages:write + public visibility)*
- ☑ **Low-friction install** — no-clone `curl` path (compose + `.env.example` + `postgres-init.sh`) and Compose V2 fallback in `install.sh`
- ☑ Rewrite `README.md` to match shipped reality (status = GA, scancode not ORT, accurate report scope, working Quick Start)
- ☑ Fix landing-page copy (`docs-site/`) — license/SBOM/CI cards corrected (EN + KO)
- ☑ Add product screenshots — README + landing showcase section
- ☑ Governance files: `GOVERNANCE.md`, `MAINTAINERS.md`, `SUPPORT.md`, `CODEOWNERS`, `.editorconfig`
- ☑ Comparison / positioning page (vs commercial SCA, vs Dependency-Track alone, vs SW360)
- ☑ Social-card (`og:image`) asset
- ☑ Fix stale ORT references in install docs (memory/cache sizing)

> **Remaining operational step before publish:** cut the first `v2.0.1` tag, then in the
> `trustedoss` org enable Actions "read and write permissions" and flip each ghcr package to
> **public**. Until a tag is published, `install-uat.yml`'s image-pull job stays `continue-on-error`.

## v2.1 — Triage Confidence (reduce Dependency-Track lock-in & noise) — shipped

Recover parity with using DT directly, and give evaluators a way to try the product.
**Shipped.**

- ☑ Surface **EPSS** scores as a first-class signal — column, sort, filter, and a policy-gate threshold
- ☑ **VEX consumption** — import OpenVEX / CycloneDX VEX to auto-suppress findings (export was already shipped)
- ☑ Live demo instance with seeded data and daily reset (read-only `DEMO_READ_ONLY` mode + GCP nightly reset Job)
- ☑ Hosted API reference (OpenAPI) on the docs site (redocusaurus at `/reference/api`)
- ☑ **Production-grade Helm chart** — Ingress/TLS, full templates, bundled-or-external PostgreSQL & Redis, pre-install/pre-upgrade migration Job, OCI/ArtifactHub publish (`charts/trustedoss` 0.2.0)
- ☑ **Evaluation profile & seed data** — low-spec (2 vCPU / 4 GB) compose profile (`docker-compose.eval.yml` + `scripts/eval-up.sh`) with seeded sample data so the product isn't an empty screen on first run, plus a `/health/ready` schema-gated readiness probe

## v2.2 — Remediation & Policy — in progress

Close the "detect → act" loop and remove the static-policy limitation.

- ☑ **Per-finding `fixed_version`** — real fixed-version data surfaced on each vulnerability finding (#153)
- ☑ **Dependency-graph depth** — direct vs. transitive classification with depth (#154)
- ◐ **Suggested dependency upgrades** — compute the minimal safe bump from `fixed_version` + dependency graph (in progress)
- ☐ **Automated upgrade PRs** (opt-in, per-ecosystem, dry-run first)
- ☐ **Dynamic license policy engine** — per-team/org editable rules (replaces the removed ORT evaluator)

## v2.3 — Supply-chain Integrity & Prioritization

Align with CISA 2025 / SLSA, and cut noise further.

- ☐ **Signed SBOMs** — cosign signatures, in-toto attestation, SLSA provenance, CISA 2025 / NTIA element coverage
- ☐ **Reachability**-based prioritization (best-effort, rolled out per language)

## v2.4 — Supply-chain Threat Detection & Deeper Prioritization

Close the biggest remaining gaps vs commercial SCA (Black Duck / Snyk / Sonatype), reusing
best-of-breed open source rather than building from scratch.

- ☐ **Malicious / typosquatting package detection** — OSSF malicious-packages + OSV `MAL-`
  feeds plus typosquat heuristics; a new `malicious` finding type that blocks the build gate
  (parity with Snyk / Sonatype Repository Firewall)
- ☐ **CISA KEV + unified Risk Score** — a known-exploited flag plus a single 0–100 score
  combining CVSS, EPSS, KEV, fix availability, and dependency depth (capstone over the v2.1
  EPSS / v2.3 reachability signals)
- ☐ **Binary scanning** (OSS-in-binary) — Syft binary classifier + Trivy filesystem mode
  feeding the existing Dependency-Track pipeline (best-effort; no modified-binary fingerprinting)
- ☐ **AI-BOM** — CycloneDX ML-BOM via cdxgen: detect AI model / dataset components and their licenses
- ☐ **Snippet / AI-generated-code origin matching** — *lowest priority, RFC-gated.* ScanOSS
  (MIT client + GPL-2.0 engine as an isolated sidecar). Requires a separate RFC on knowledge-base
  hosting and fingerprint egress before any work starts.

---

## Explicitly out of scope

- **Building our own vulnerability database** — we continue to aggregate through Dependency-Track,
  augmented by EPSS, CISA KEV, and VEX.

> Note: snippet / full-text origin detection (ScanOSS-style), previously out of scope, is now
> tracked at the **lowest priority** under v2.4 (RFC-gated) rather than excluded outright.

## Backlog (not yet scheduled)

SSO / OIDC, native Jenkins plugin, Excel reports, compliance PDF, historical-scan pinning on SBOM/NOTICE.

---

Roadmap items are proposals, not commitments — dates are deliberately omitted. Feedback and
contributions are welcome: open a [discussion](https://github.com/trustedoss/trustedoss-portal/discussions)
or an issue referencing the relevant milestone.
