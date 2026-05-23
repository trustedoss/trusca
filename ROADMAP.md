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

## v2.1 — Triage Confidence (reduce Dependency-Track lock-in & noise)

Recover parity with using DT directly, and give evaluators a way to try the product.

- ☐ Surface **EPSS** scores as a first-class signal — column, sort, filter, and a policy-gate threshold
- ☐ **VEX consumption** — import OpenVEX / CycloneDX VEX to auto-suppress findings (we already export VEX)
- ☐ Live demo instance with seeded data and daily reset
- ☐ Hosted API reference (OpenAPI) on the docs site
- ☐ **Production-grade Helm chart** — Ingress/TLS, full templates, OCI/ArtifactHub publish (current chart is a 0.1.0 scaffold)
- ☐ **Evaluation profile & seed data** — low-spec compose profile + optional sample data so the product isn't an empty screen on first run

## v2.2 — Remediation & Policy

Close the "detect → act" loop and remove the static-policy limitation.

- ☐ **Suggested dependency upgrades** — compute the minimal safe bump from `fixed_version` + dependency graph
- ☐ **Automated upgrade PRs** (opt-in, per-ecosystem, dry-run first)
- ☐ **Dynamic license policy engine** — per-team/org editable rules (replaces the removed ORT evaluator)

## v2.3 — Supply-chain Integrity & Prioritization

Align with CISA 2025 / SLSA, and cut noise further.

- ☐ **Signed SBOMs** — cosign signatures, in-toto attestation, SLSA provenance, CISA 2025 / NTIA element coverage
- ☐ **Reachability**-based prioritization (best-effort, rolled out per language)

---

## Explicitly out of scope

- **Snippet / full-text origin detection** (ScanOSS-style). We stay with declared (cdxgen) + detected
  (scancode) license detection. May be revisited later via a separate RFC.
- **Building our own vulnerability database** — we continue to aggregate through Dependency-Track,
  augmented by EPSS and VEX.

## Backlog (not yet scheduled)

SSO / OIDC, native Jenkins plugin, Excel reports, compliance PDF, historical-scan pinning on SBOM/NOTICE.

---

Roadmap items are proposals, not commitments — dates are deliberately omitted. Feedback and
contributions are welcome: open a [discussion](https://github.com/trustedoss/trustedoss-portal/discussions)
or an issue referencing the relevant milestone.
