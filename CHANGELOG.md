# Changelog

All notable changes to TrustedOSS Portal are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Documentation
- **Wave 7 documentation parity.** New pages, EN + KO: a **Triage** guide
  (`user-guide/triage.md`) that consolidates how a finding flows across VEX
  vulnerability triage, component approval, and the build gate — and makes
  explicit that a rejected component approval does not gate the build; an
  **Analysis types** reference (`reference/analysis-types.md`) matrix of source
  SBOM scan / container scan / policy gate / (planned) reachability; a
  **Best practices** category (scan frequency, policy design, team structure,
  upgrade cadence); and an **FAQ** (`reference/faq.md`) link hub. Also wired the
  previously-unlisted v0.13.1 and v0.14.0 release notes into the sidebar.

### Added
- **Container scans surface base-image OS end-of-life (EOSL).** Trivy reports
  whether a scanned image's base OS release is past its end-of-service-life —
  it no longer receives upstream security fixes, so newly disclosed CVEs will
  never be patched on it, a risk that no individual package CVE captures. We
  now persist that OS family/release + EOSL flag (into `scan_metadata`, no
  migration) and show an "OS end-of-life" panel on the scan detail page when
  the release is EOL. The verdict comes from Trivy's bundled database (no extra
  network), so a stale database may not yet flag a recently retired release.

### Fixed
- **Dynamic-scan sidecar now targets the git-clone root.** With
  `SCAN_EXECUTOR=local_docker`, an Android scan of a **git** repository routed
  to the sidecar but read the compileSdk and ran the build from the outer
  workspace directory instead of the clone root a level below — so it picked
  the default SDK image and scanned an empty directory. (Language detection is
  non-recursive and the sidecar targets a single directory, unlike the default
  in-process cdxgen which recurses.) The scan executor now carries the resolved
  project root and the sidecar uses it. Only affects the opt-in `local_docker`
  executor; the default in-process path was unaffected.

### Added
- **License enrichment now covers RubyGems and NuGet, and is air-gap gated
  (`LICENSE_FETCH_ENABLED`).** When cdxgen emits a component with no SPDX
  license — the common case for a bare `requirements.txt`, `Gemfile`, or
  `.csproj` — the pipeline looks the license up in the component's public
  registry and records it as a *concluded* finding, pulling the "unknown"
  license ratio down. Gem (`rubygems.org`) and NuGet (`api.nuget.org`)
  fetchers join the existing PyPI / Maven / crates.io / pkg.go.dev set, so
  Ruby and .NET dependencies are no longer 100% unknown. The lookup was
  previously unconditional scan-time egress; it now respects an
  `LICENSE_FETCH_ENABLED` flag (default **on** — only a package name+version
  leaves the network) so an air-gapped deployment sets it `false` to skip the
  fetch cleanly instead of paying a per-component network timeout.
- **Korean license content (summaries + obligations).** When the interface
  language is Korean, each classification-catalog license's plain-language
  summary and its obligation text now render in Korean, with the authoritative
  English original one click away (the canonical license text stays English).
  Covers the finite 52-license catalog; licenses outside it fall back to
  English. No schema change — the translations live in a code catalog and are
  attached to the API responses, with an EN↔KO drift contract test.
- **Policy-aware SBOM export profiles.** `GET /v1/projects/{id}/sbom` accepts
  an optional `profile`: `policy-annotated` flags each component that violates
  the project's effective license policy in place (CycloneDX `properties` /
  SPDX annotations, for forbidden and conditional licenses); `policy-filtered`
  drops forbidden components (and the vulnerability entries referencing them),
  recording the excluded count on the document. The default export is
  unchanged and byte-stable. Profile exports are **not** cosign-signed — the
  signature covers only the canonical default SBOM.

## [0.14.0] — 2026-07-16

### Added
- **Audit trail for external side effects.** Posting/updating a gate PR
  comment (`sca_pr_comment.posted` / `.updated`) and uploading a source
  archive (`source_archive.uploaded`) now write explicit audit rows with
  full request context — both actions previously left no trail because the
  automatic audit listener only sees DB rows. Explicit rows also run their
  diff through the sensitive-column masker.
- **Global mutation error toast (frontend).** A cache-level error handler
  guarantees no failed write stays silent: any mutation that does not
  surface its own error now raises an error toast with the RFC 7807
  `detail`. Existing call sites keep their local error UX via an explicit
  opt-out; 422 validation problems stay inline per the design system. The
  ErrorBoundary fallback is now translated (EN/KO) and announced via
  `role="alert"`.
- **EOL operations: weekly refresh beat + admin health panel.** A weekly
  Celery beat re-stamps the component catalog against the newest
  endoflife.date snapshot (so release upgrades reach existing rows without
  a re-scan, and stamps are cleared when the whitelist shrinks) and — only
  when `EOL_REFRESH_ENABLED=true`, off by default — fetches fresh lifecycle
  data with a sanity floor that stops a gutted sweep from displacing a good
  dataset. The admin/health page gains an endoflife.date snapshot panel
  (dataset age with a 180-day stale warning, flagged totals, last tick,
  next fire) at `GET /v1/admin/eol/health`.
- **End-of-life (EOL) component flagging.** Components matching a curated
  endoflife.date product whitelist (Spring Boot, Express, Django, Rails,
  Angular, Vue, Next.js, Symfony, Laravel, Spring Framework) are stamped
  with their lifecycle verdict on the shared catalog. The Components tab
  gains an EOL column/badge and an "EOL only" filter (`?eol=true`), the
  drawer an End-of-life row, and the project Overview an EOL count that
  deep-links to the filtered list. Verdicts come from a snapshot vendored
  with the release — zero network at scan time, air-gap safe
  (`EOL_SNAPSHOT_PATH` mounts a fresher snapshot; `EOL_ENABLED=false`
  disables).
- **iOS CocoaPods/SPM lockfile scanning.** A `Podfile` used to crash the
  whole source scan (cdxgen's cocoapods cataloger throws without the `pod`
  CLI). The scanner now excludes that cataloger and reconstructs pods —
  components AND dependency graph, subspecs included — offline from the
  committed `Podfile.lock`. Repos with only a committed `Package.resolved`
  now route to the swift environment, and the sidecar executors no longer
  re-run `swift package resolve` over a committed lockfile.
- **Runtime-scope SBOM filtering (default ON).** Source scans now drop
  non-deployable dependencies from the cdxgen SBOM before persist, signing and
  vulnerability matching: Maven `test`/`provided` nodes (cdxgen scope tags
  `optional`/`excluded`) and npm `devDependencies` (lockfile-classified `dev`).
  CVE counts and license obligations now describe the artifact that actually
  ships. **Component and CVE counts drop on the first re-scan of affected
  Maven/npm projects** — the scan summary records how many components were
  excluded, and `SCAN_SCOPE_FILTER_ENABLED=false` (or the per-ecosystem
  `SCAN_SCOPE_FILTER_MAVEN_ENABLED` / `SCAN_SCOPE_FILTER_NODE_ENABLED`)
  restores the full graph. SBOMs uploaded via the ingest API are never
  filtered — an uploaded SBOM is the supplier's declared truth.

### Fixed
- **Source-archive zip-ratio guard no longer rejects real OSS trees.** The
  flat 200x per-member compression-ratio ceiling blocked archives carrying
  tiny sparse fixtures (Juice Shop 17.0.0 ships two test PDFs at 918x/940x
  that inflate to ~150–225 KB). The ceiling now applies only to members
  declaring more than `SOURCE_ARCHIVE_RATIO_GUARD_MIN_BYTES` uncompressed
  (default 10 MiB); the streamed total-extracted cap remains the
  authoritative bomb guard and 42.zip-class members are still rejected. The
  rejection message now names the member and the resolving env knobs.
- **`seed_demo --demo-only` restores the documented 5-project quickstart.**
  The verify-baseline fixtures the default seed creates (per the
  seed-baseline agreement) had pushed the visible project list to 8, and
  the docs-uat quickstart-gate had failed nightly since 2026-06-10. The
  quickstart guide's seed command now uses the flag; the default seed is
  unchanged for the verify-specs nightly and Tier-3 runs.

### Security
- **Worker Go toolchain 1.25.11 → 1.25.12** — clears Go stdlib
  CVE-2026-39822 (`os.Root` symlink-following directory traversal, HIGH)
  on the bundled Go binaries and govulncheck. cosign/docker CLI carry the
  same stdlib finding with no upstream rebuild available yet — suppressed
  as UNREACHED with a re-evaluate deadline (`.trivyignore`).

## [0.13.1] — 2026-07-07

A fixes-only patch release: repairs fresh role-separated (L1) installs and
deployments, and picks up a security bump. No feature or schema changes.

### Fixed
- **L1 role provisioning: `trustedoss_app` was never created.**
  `scripts/postgres-init.sh` interpolated the role name / password inside a
  dollar-quoted `DO $$ … $$` block, where psql performs no variable
  substitution, so the literal `:'app_user'` reached the server and aborted the
  init script (`syntax error at or near ":"`). Every L1 backend then failed
  password auth as `trustedoss_app`. Role creation now uses `SELECT format(…)
  … \gexec` with `WHERE NOT EXISTS` — SQL-quoted and idempotent. (#466)
- **`AUTO_MIGRATE` was never plumbed into the container.** `install.sh` writes
  `AUTO_MIGRATE=false` on L1 stacks (migrations run once as the owner role), but
  the compose file never referenced `${AUTO_MIGRATE}`, so the backend entrypoint
  defaulted back to `true` and attempted DDL as the unprivileged app role. (#466)
- **install.sh L1 path: owner-password consistency + staged boot.** The secret
  block is now idempotent — `POSTGRES_PASSWORD` is the single source of truth for
  the owner password and is never rotated on re-run — and boot is staged
  (postgres+redis+backend → wait `/health` → owner-role `alembic upgrade head` →
  wait `/health/ready` → full fleet) so the worker's `depends_on backend:
  service_healthy` no longer deadlocks under `AUTO_MIGRATE=false`. A fresh
  install now also generates a strong random owner password instead of the
  shipped default. (#470)
- **Dev backend image now auto-migrates.** `apps/backend/Dockerfile` had a `CMD`
  but no `ENTRYPOINT`, so the dev container skipped `docker-entrypoint.sh` and
  never ran its migration — `/health/ready` stayed 503 and the backend was
  permanently unhealthy. It now carries the entrypoint like the production
  image. (#469)

### Security
- **`python-multipart` 0.0.30 → 0.0.31** — picks up the fix for CVE-2026-53540.
  Ships in the backend image. (#472)

### Internal
- Release notes are now stripped of Docusaurus front matter before being
  published to GitHub Releases (the closing `---` was rendering the metadata
  block as a giant heading). (#473)
- Release-gate CI hardening: cold-boot postgres readiness race, dev-runtime
  boot mode, docker-compose V1 nested-DSN interpolation, and health-gated
  (not `up`-exit-gated) patience. (#462, #463, #464, #465)
- Added L1 role-separated `install-uat` coverage and a `postgres-init` role
  contract gate. (#467, #468)

## [0.13.0] — 2026-07-04

A broad parity release closing the BomLens capability gap — additive throughout;
the one external-egress capability (SCANOSS) is off by default.

### Added
- **SCANOSS vendored-OSS identification (opt-in, off by default)** — an optional
  scan stage that fingerprints the source tree and matches copied-in
  ("vendored") open source against the SCANOSS knowledge base, recording
  full-file matches as components with detected licenses. This closes the gap
  for C/C++ / embedded trees that have no package manifest, where cdxgen alone
  finds almost nothing. It is **disabled by default** and gated on
  `SCANOSS_ENABLED=true`: unlike a local dev tool, a self-hosted portal must not
  send file fingerprints to an external API without explicit operator consent.
  When enabled, only file **fingerprints** (never source) are sent to
  `SCANOSS_API_URL` (default `api.osskb.org`, overridable for a self-hosted
  SCANOSS); snippet matches are skipped to keep results clean, and the stage
  degrades to a no-op on any error so a scan never fails because of it.
- **Global search (⌘K)** — the command palette (⌘K / Ctrl+K) gains cross-project
  **Components** and **CVEs** groups alongside Projects and Pages, backed by the
  new `GET /v1/search` endpoint. Results are scoped server-side to the caller's
  teams through a single `team_scope_filter` chokepoint — another team's
  components or vulnerabilities never appear. Component hits deep-link to the
  project's Components tab filtered to the term; CVE hits to its Vulnerabilities
  tab. Queries run from two characters, debounced, capped at 20 per group.
- **Dependency graph view** — the Components tab gains a **Table / Graph** toggle.
  The graph view renders the scan's resolved dependency graph (every parent →
  child edge the scanner recorded) as an interactive cytoscape node-link diagram
  with a severity-coloured node per component, a search highlight, and a
  click-to-detail panel — backed by the new
  `GET /v1/projects/{id}/dependency-graph` endpoint (serialised from the existing
  `component_dependency_edges` table; no migration). The choice mirrors into
  `?view=graph`. Graphs past the server node cap
  (`DEPENDENCY_GRAPH_MAX_NODES`, default 5000) or with no recorded edges fall
  back to a banner / collapsible tree so the view stays usable at scale.
- **Excel (`.xlsx`) vulnerability report** — the project vulnerability report can
  now be downloaded as an Excel workbook in addition to PDF, from the **Excel**
  button on the Reports tab's Vulnerability-report card (or
  `GET /v1/projects/{id}/vulnerability-report.xlsx`). The workbook has three
  sheets — Overview (risk score, severity + license distribution), Components,
  and Vulnerabilities (CVE, CVSS, EPSS, KEV state + due date, affected
  component) — and each download is recorded in the export history as
  `vuln_xlsx`. Cell values sourced from scanned third-party metadata are
  neutralised against spreadsheet formula injection (a value starting with
  `= + - @` is written as literal text — CWE-1236). This closes the CLAUDE.md
  "Excel / PDF reports" commitment, which previously shipped PDF only.
- **License classification catalog expansion (32 → 52 licenses)** — the license
  categoriser, obligation catalog, and bundled full-text set grew by 20 common
  SPDX licenses so fewer components land as `unknown`. New allowed (permissive)
  entries: BSL-1.0, Artistic-2.0, PostgreSQL, X11, NTP, Ruby, PHP-3.01, UPL-1.0,
  MIT-0, BlueOak-1.0.0, AFL-3.0, MS-PL, Libpng, CC-BY-4.0, curl, OpenSSL,
  BSD-4-Clause; new conditional (share-alike / reciprocal) entries: OFL-1.1,
  CC-BY-SA-4.0, MS-RL. Each ships its structured obligations and its verbatim
  SPDX full text for the NOTICE. A component that declares a license by
  **free-text name** with no SPDX id (e.g. `"Apache License, Version 2.0"`) is
  now run through an alias normaliser (ported from BomLens `spdx-normalize.jq`)
  and recovered as its canonical id when the name is a recognised alias;
  unfamiliar names stay `unknown` rather than being guessed. The three-way
  set (categoriser ↔ catalog ↔ bundled texts) is locked by a contract test.
- **AI license review flags** — the license catalog now carries two advisory
  "review needed" flags for AI-relevant restrictions that standard open-source
  compliance tooling misses: `behavioral_use` (RAIL / OpenRAIL and the Llama,
  Gemma, and Falcon community model licenses — behavioral-use restrictions) and
  `non_commercial` (CC-BY-NC and similar non-commercial terms). Flagged licenses
  show an amber "Review needed" badge and filter on the Compliance tab, and the
  generated NOTICE document gains a "License review needed" section. The flags
  report only the *existence* of a restriction class — whether it applies to a
  given use is a human / legal judgment (BomLens `license-flags.jq` /
  OpenChain AI SBOM principle). Ordinary licenses (MIT, Apache-2.0, GPL) are not
  flagged.
- **NOTICE license texts + per-component copyright** — the NOTICE document
  (text / markdown / html) now closes with a "License Texts" section embedding
  the full SPDX text of every license observed in the project (50+ license
  texts bundled — see the catalog-expansion entry above; a license without a
  bundled text falls back to its reference-URL link), and each component line carries the copyright statement
  recorded in the scan's SBOM — or, when the SBOM recorded none, an explicit
  fallback pointing at the component's registry URL (the line is never blank).
  The NOTICE artifact thereby satisfies the obligation catalog's
  `license_text_inclusion_required` obligation.
- **G7 AI SBOM minimum-elements conformance (advisory)** — SBOM ingest now
  accepts CycloneDX `specVersion` 1.7 (the ML-BOM model-card fields), and when
  an uploaded document carries a `machine-learning-model` component the
  conformance verdict appends the 51 G7 "SBOM for AI" minimum-element checks
  (7 clusters: metadata, system level properties, models, datasets properties,
  infrastructure, security properties, key performance indicators). Each
  element reports pass (present), advisory warn (absent), or "requires human
  review" (no automated source); G7 entries carry `cluster` / `source` /
  `role` / `evidence` fields in the `checks[]` array. All 51 are advisory —
  the overall pass / warn / fail verdict and its counters are unchanged.
  Registry and check semantics are vendored from BomLens
  (sktelecom/sbom-tools, Apache-2.0). No new env keys.
- **CISA KEV surfacing + Priority sort** — findings whose CVE is listed in the
  CISA KEV (Known Exploited Vulnerabilities) catalog carry a **KEV** badge and
  the catalog's remediation due date (`kev_due_date`) in the findings table and
  drawer. A new **Priority** sort — KEV first, then severity, then EPSS — is the
  default ordering. A daily Celery beat task (`trustedoss.kev_catalog_refresh`)
  syncs the CISA feed (~1,600 entries) into the vulnerability catalog,
  delistings included. New env keys: `KEV_FEED_URL`, `KEV_REFRESH_ENABLED`
  (set `false` on air-gapped deployments — KEV badges are then not shown), and
  `KEV_REFRESH_TIMEOUT_SECONDS`.
- **KEV operations closeout — admin feed panel + due-date status** —
  `/admin/health` gains a **KEV feed** panel: last successful sync time, live
  KEV-listed CVE count, listed / delisted counts from the last run, the next
  daily sync (01:45 UTC beat), and an OK / skipped (+reason) / disabled /
  never-run status backed by a new single-row `kev_sync_state` table the beat
  task upserts on every tick. A parsed feed below the 500-entry sanity floor
  is skipped like an outage (`skipped_reason: feed_below_sanity_floor`),
  preserving existing KEV flags so a gutted or truncated feed document can
  never mass-delist the catalog. The KEV badge in the findings table and
  drawer now grades the CISA remediation due date into three states — overdue
  (red) / due within 7 days (amber) / on track (neutral) — with a `D-n` /
  `D+n` day count.

### Fixed
- **Source scans no longer misclassify transitive dependencies as direct** when
  cdxgen emits the SBOM root's `dependencies` entry with an empty `dependsOn`
  (observed on Maven / Gradle source scans). The depth computation now trusts
  the metadata root only when it declares children and otherwise falls back to
  in-degree-0 root detection, so the Components TYPE column shows the real
  direct set. (#435)

## [0.12.0] — 2026-06-15

Two feature themes: **received-SBOM ingest with conformance scoring** (a customer
hands TRUSCA an SBOM their own tooling produced) and an **on-prem dynamic
per-environment scan executor** (the worker can launch a per-environment cdxgen
sidecar for a toolchain it does not carry, closing the Android gap). Both are
additive and opt-in — existing scans are unchanged.

Model 3 — **received-SBOM ingest with conformance scoring**. A customer can hand
TRUSCA an SBOM their own tooling already produced (rather than having TRUSCA
clone and build the source), and TRUSCA validates its quality, matches CVEs,
classifies licenses, and runs the build gate on it.

### Added
- **Received-SBOM ingest endpoint** — `POST /v1/projects/{project_id}/sbom-ingest`
  accepts an uploaded SBOM and queues an `sbom`-kind scan that persists the
  SBOM's components, matches CVEs with Trivy, and classifies declared licenses —
  no source clone or build. API-key or JWT auth, one in-flight scan per project,
  and the usual size / structure guards. (#404, #406)
- **SPDX input support** — both CycloneDX-JSON and SPDX (JSON and Tag-Value) are
  accepted. Trivy auto-detects the format for CVE matching; SPDX is mapped to
  CycloneDX internally for the component graph (no `spdx-tools` dependency).
  SPDX RDF/XML is not accepted. (#411)
- **SBOM conformance scoring** — every uploaded SBOM is scored for quality on its
  original bytes and gets a **pass / warn / fail** verdict. Mandatory checks:
  timestamp, tool info, a top-level component, 100% component name+version, PURL
  coverage ≥ `SBOM_CONFORMANCE_PURL_MIN_PCT` (default 90), no `pkg:generic`
  placeholders, and a transitive dependency graph; license and hash coverage are
  recommended (warn-only). The verdict is **advisory** — a `fail` is recorded and
  surfaced but does not block matching. Stored per scan, exposed at
  `GET /v1/projects/{project_id}/scans/{scan_id}/conformance`, and rendered as a
  badge + per-check table on the scan detail page. (#409, #410, #412)
- **`sbom` scan kind** in the UI — badge and admin queue filter label the new
  scan kind (EN / KO). (#408)

### Changed
- The `scan_kind` enum gained the `sbom` value, and the shared back-half of the
  source pipeline (component persistence → Trivy matching → finalize) was
  extracted to `tasks/_scan_pipeline` so the ingest task reuses it. (#404, #405)

### Documentation
- New CI-integration guide **Upload an SBOM** (endpoint, formats, conformance
  verdict; EN / KO), and the user-guide **Scans** / **SBOM** pages now document
  the `sbom` scan kind, received-SBOM upload, and the conformance verdict. (#413)

---

**Dynamic per-environment scan executor** (BomLens-style, on-prem). The
SBOM-generation stage is now pluggable: instead of always running cdxgen in the
worker, the worker can launch a per-environment **sidecar** container for a
toolchain it does not carry. Opt-in and on-prem single-tenant only; the default
is unchanged.

### Added
- **`SCAN_EXECUTOR=local_docker`** — an opt-in executor that launches a
  per-environment cdxgen sidecar over the host Docker socket, runs build-prep +
  cdxgen there, and collects the SBOM. The default `inprocess` executor is
  byte-for-byte unchanged. Behind a `ScanExecutor` abstraction with environment
  detection ported from BomLens `source-detect.sh`. (#417, #418, #419)
- **Android dependency-graph scanning** — the worker has no Android SDK, so the
  Android Gradle Plugin cannot resolve dependencies (0 components). Routing
  Android to the `sbom-scanner-android-sdk<API>` sidecar resolves the full graph
  (verified 0 → 67 components on a sample). Android is the default routed
  environment; the routed set is configurable via `SCAN_LOCAL_DOCKER_ENVS`. (#419, #422)
- **cdxgen output toggles** — `CDXGEN_SPEC_VERSION` (1.5 default, set 1.6) and
  `CDXGEN_FETCH_LICENSE` (off by default) tune the SBOM spec version and
  component-license resolution, applied by both the in-process and sidecar paths. (#420)
- **Sidecar security hardening** — `named` workspace-only volume mounting by
  default (never the cosign key), `--cap-drop=ALL` + the minimal build set,
  `no-new-privileges`, default memory / CPU / pids bounds, a curated env
  allow-list (no worker secrets), refusal of unpinned `:latest` images, an
  isolated egress network, an opt-in Docker socket proxy, and PEM-key redaction
  on sidecar output. Passed a security-reviewer Producer-Reviewer. (#421)

### Changed
- Generalized the sidecar executor from Android-only to any detected
  environment. Verification on Colima showed our all-in-one worker resolves
  transitive dependencies for node / go / rust / ruby / java / python / php /
  dotnet **identically** to the dedicated cdxgen language images, so those route
  only for per-build isolation (opt-in), not detection — Android is the one
  genuine gap and the only default-routed environment. (#422)

### Documentation
- New admin-guide page **Dynamic scan executor** (security model, in-code
  containment defaults, opt-in setup; EN / KO). A deferred implementation plan
  for the SaaS Kubernetes Job executor is recorded in
  `docs/dynamic-scan-k8s-executor-plan.md`. (#421, #423)

## [0.11.1] — 2026-06-13

A UI / branding patch release. No backend or API changes — only the frontend
image, docs, and Helm chart metadata change versus `0.11.0`.

### Changed
- **Theme reverted to the W11 light theme.** The W13 "Google AI Studio"
  re-skin shipped in `0.11.0` (white canvas, blue primary, pill buttons) is
  rolled back to the W11 Vercel + Linear look (off-white canvas, warm
  near-black primary, square corners, blue Low badge). The TRUSCA brand and
  rename are unaffected.
- **New logo.** The mark is now a dark-slate tile (`#0f172a`) with a teal
  check accent (`#2dd4bf`) and an ink "TRUSCA" wordmark; the full lockup adds
  the tagline "TrustedOSS SCA" on the login gateway. Replaces the earlier
  flat-black and teal-gradient marks.
- **Complete favicon set.** Added `favicon.ico` (16 / 32 / 48) and an
  `apple-touch-icon.png` (iOS home screen) alongside the existing SVG, wired
  into `index.html` with a `theme-color`. Previously SVG-only.

### Fixed
- **Helm chart icon URL.** `Chart.yaml`'s `icon:` pointed at a non-existent
  path (`docs/static/.../logo.png`); it now resolves to
  `docs-site/static/img/logo.png` (a new 256×256 raster of the mark).

### Docs
- Regenerated the docs Open Graph social card with the new logo; added a
  README header logo; refreshed the design-system and brand reference pages.

## [0.11.0] — 2026-06-12

The first post-GA feature release. Headlines: the product is **renamed to
TRUSCA**, a public **read-only demo SaaS** deployable to a single Hetzner
server, a UI **craft pass** (W11–W12), and a hardening sweep from an external
verification campaign.

### Renamed — TrustedOSS Portal is now TRUSCA

**TRUSCA** (Trust + SCA) is the new product name — *the SCA tool of the
TrustedOSS initiative*. The umbrella initiative keeps the TrustedOSS name; the
tool gets its own. What changes for you:

- **Repository**: `github.com/trustedoss/trustedoss-portal` →
  `github.com/trustedoss/trusca`. Git remotes and old web links redirect
  automatically.
- **Docs site path**: `trustedoss.github.io/trustedoss-portal/` →
  `trustedoss.github.io/trusca/` (GitHub Pages does **not** redirect the old
  path — update bookmarks).
- **Container images** (BREAKING for upgrades): from 0.11.0 images publish as
  `ghcr.io/trustedoss/trusca-backend`, `trusca-backend-worker`, and
  `trusca-frontend`. Releases ≤ 0.10.0 keep their old image names, and an
  upgrade via `git checkout v0.11.0 && bash scripts/upgrade.sh` switches
  automatically (the new compose file pins the new names). Only custom
  overlays that hardcode the old image names need a manual edit.
- **Unchanged on purpose**: database user/roles, the Celery app name, the
  compose network, demo account e-mails, and `urn:trustedoss:*` problem URNs
  are internal identifiers that match the umbrella name and stay as-is.
- New brand: the "Hex Check" mark (package hexagon + verification check) and
  the first frontend favicon.

### Added
- **Public read-only demo mode** — `DEMO_READ_ONLY` makes the backend serve all
  reads but reject every write (allow-listing only the auth login/refresh/logout
  flow) with an RFC 7807 403. The SPA surfaces it as a banner, a login-page
  credentials hint, and a dedicated "read-only demo" toast on blocked writes.
- **Hetzner demo provisioning** — cloud-init, an operator runbook (EN/KO), an
  idempotent `seed_demo` dataset, a daily `reset_demo` wipe-and-reseed timer, and
  a daily backup timer.
- **Optional SSH-based CD** (`deploy-hetzner.yml`) — one-click / on-release deploy
  to the demo host via the existing `upgrade.sh`, with strict tag validation and
  host-key pinning.
- **Day-2 operations** — opt-in offsite backup (`backup-offsite.sh`, rclone), a
  backstop uptime canary workflow, and a Korean translation-style linter for the
  docs site.

### Changed
- **Visual & craft pass (W11–W12)** — modern-enterprise theme (warm near-black
  primary, off-white canvas), Inter/JetBrains-Mono typography system, an
  in-house global toast, CSS-only route/motion transitions with a reduced-motion
  guard, and richer empty/loading states.

### Fixed
- Drawer obligations, CVE deep-links, and the Compliance NOTICE toolbar
  (M-20/M-21/M-22). Relative-time displays now always carry an absolute-time title.

### Security
- Revoke the entire refresh-token family on reuse detection (C-1).
- Redact embedded `git_url` credentials on the read API and in audit logs (C-2).
- Enforce the project boundary for project-scoped API keys (M-2) and scope
  `GET /v1/audit` reads to the caller's team for team admins (M-3).
- Codified five testing-hardening rules and vendored the verification team's
  deterministic specs as a nightly regression gate.

## [0.10.0] — 2026-05-31

First public release of TrustedOSS Portal.

### Scope

A self-hosted, Apache-2.0 SCA portal covering vulnerability tracking,
license compliance, SBOM generation, and CI/CD integration in one UI.

### Highlights

#### Scanning
- **Source scans** — `cdxgen` generates a CycloneDX SBOM across 30+ language
  ecosystems; Trivy correlates components against its unified vulnerability DB
  (NVD + OSV + GitHub Advisory + EPSS + KEV).
- **Container scans** — Trivy on OS packages of an image reference.
- **Vulnerability re-detection** — weekly Trivy DB refresh + a Celery beat
  re-matches existing SBOMs against the refreshed feed, with notification
  channels firing on new criticals.
- **Air-gapped support** — `TRIVY_DB_REPOSITORY` can point at a private OCI
  mirror of the Trivy DB.
- **Scan retention** — results are keyed by project ref so each ref keeps its
  latest scan + findings (superseded scans retired automatically, a beat
  reclaims orphans, and manual `DELETE` is available) — no unbounded growth.

#### Compliance
- **License classification** — allowed / conditional / forbidden tiers,
  scored against a fixed catalog.
- **Obligations** — auto-generated `NOTICE` files (text / markdown / HTML).
- **Component approval workflow** — Pending → Under Review → Approved / Rejected.
- **VEX** — export and consumption (OpenVEX + CycloneDX VEX), 7-state triage.
- **SBOM export** — CycloneDX (JSON/XML) and SPDX (JSON/Tag-Value), byte-stable,
  with per-component license and version fields populated.
- **Forbidden-license waivers** — time-boxed waivers from the Compliance tab,
  capped by `LICENSE_WAIVE_MAX_DAYS` so a waiver cannot outlive its review.

#### CI/CD
- **GitHub Actions composite action** (`actions/scan/`) — trigger a scan and
  gate the build on Critical CVEs or forbidden licenses (`exit 1`).
- **GitHub & GitLab webhooks** — auto-trigger scans on push / PR events with
  inline PR/MR comments.
- **REST API + API Keys** — for Jenkins and other CI systems without a native
  integration; a Jenkinsfile example is shipped.
- **EPSS prioritization** — column, sort, filter, and a policy-gate threshold
  (`GATE_EPSS_THRESHOLD`).
- **API key expiry presets** — pick a TTL when minting a key from the
  Integrations form; keys carry an explicit expiry.
- **Self-scan hardened via dogfooding** — running our own scan-action against
  this repo surfaced and fixed an API-key scope rejection on trigger/poll
  (`401`) and a disjunctive-`OR` license misclassification.

#### Operations
- **Multi-tenant teams + RBAC** — `super_admin` / `team_admin` / `developer`.
- **Append-only audit log** — every write surfaced with diff + actor; SQL-level
  immutability via a `plpgsql` trigger.
- **Notifications** — email (SMTP), Slack, Microsoft Teams.
- **Admin UI** — user/team management, Trivy DB monitoring + weekly refresh,
  scan queue, disk dashboard, audit-log search/filter/CSV export.
- **Backups** — daily auto-backup via Celery beat + manual backup/restore from
  the Admin UI.
- **Self-hosted demo mode** — `DEMO_READ_ONLY=true` makes the deploy read-only.

#### Experience
- **EN + KO i18n** — every UI string and every documentation page is shipped
  in both languages from the first public release.
- **Modern enterprise design system** — light theme, WCAG AA contrast,
  compact 40 px tables, drawer + page navigation dual surfaces.
- **Filter URL persistence** — every filter facet (severity, license category,
  search, status, page) lives in the URL so reload / share / back-button
  restores the exact view.
- **Global ⌘K palette** — keyboard-first navigation across projects, vulns,
  components, and admin surfaces.
- **Portfolio Dashboard** — KPI cards + severity / license distribution +
  recent scans / activity, on `/`.
- **Collapsible sidebar + responsive shell** — the sidebar toggles to a 64 px
  icon rail (persisted) and collapses to a hamburger drawer below `lg`.

#### Distribution
- **Docker Compose** (dev + prod with Traefik + Let's Encrypt).
- **Helm chart** (`charts/trustedoss`) — bundled-or-external PostgreSQL &
  Redis, Ingress with cert-manager TLS, migration Job.
- **Hosted OpenAPI reference** at `/reference/api` on the docs site.
- **`/health/ready`** — schema-gated readiness probe; `503` until the Alembic
  schema is at HEAD.
- **Chart image tags pinned to the release** — `image.tag` defaults track
  `appVersion` (`0.10.0`) so a default `helm install` pulls matching images.

#### Quality
- **Documentation UAT harness** — the user/admin/CI guides are exercised
  end-to-end ("does it work as written?") with 38 auto-executed assertions
  across 23 enrolled docs, run nightly.
- **CI gates re-enabled** — SAST (Semgrep / Bandit), the Playwright e2e matrix,
  and supply-chain self-scan run on every change or nightly, with `main`
  branch protection enforcing the required checks.
