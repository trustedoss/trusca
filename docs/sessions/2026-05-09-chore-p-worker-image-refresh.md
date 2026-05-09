# Session Handoff — Chore P (Phase 8 worker-image refresh)

**Date**: 2026-05-09
**Branch**: `chore/phase8-worker-image-refresh`
**PR**: TBD (will be filled in after `gh pr create`)
**Predecessor sessions**:
- `2026-05-09-chore-e-install-uat.md` (Chore E shellcheck + install/restore UAT)
- `2026-05-09-chore-n-uat-scenarios.md`, `2026-05-09-chore-o-security-review.md`, `2026-05-09-chore-m-docs-refresh.md`

## Goal

Close the last Phase 8 hardening backlog item:

- `Dockerfile.worker` base image dependency bump (Python / Go SDK / ORT / Gradle / npm).
- `.trivyignore` cleanup — drop entries the bumps fix; refresh re-evaluate dates on the rest.
- `.github/workflows/ci.yml` — promote Trivy HIGH from advisory to hard-fail (single combined `severity: CRITICAL,HIGH` step).

PR #30 (Chore H) already brought CRITICAL behind a hard-fail; HIGH had been kept advisory while the multi-language toolchain bundle's residual findings were reach-analysed.

## Version table — before / after

| Component | Before | After | Why |
|---|---|---|---|
| `python:3.12.7-slim` | 3.12.7 | **3.12.13** | 6 patch CPython security releases; same bookworm-slim base; api Dockerfile stays on 3.12 → ABI shared |
| Go SDK | 1.22.7 | **1.25.10** | Fixes CVE-2025-68121 (CRITICAL, crypto/tls session-resumption) — the only ignored CRITICAL in `.trivyignore` is now resolved by SDK bump. 1.25.x for the longest support window without taking 1.26 (limited soak time) |
| Gradle | 8.10.2 | **8.14.3** | Latest 8.x. Stayed on 8.x rather than 9.x because 9 dropped Java 8/11/16 source compat — would force every consumer's `gradle dependencies` enumeration through a compat check |
| npm | 11.13.0 | **11.14.1** | Latest 11.x; same drop-cross-spawn / bundled-tar 7.x rationale; 11.14.1 picks up further bundled-dep patches |
| ORT | 85.0.0 | **85.1.1** | 85.x patch — JRE 21 unchanged, `ort evaluate` CLI unchanged. 85.1.x did NOT refresh json-smart / msgpack-core (those `.trivyignore` entries stay) |
| cdxgen | 12.3.3 | 12.3.3 | Already npm registry latest |
| Trivy | 0.70.0 | 0.70.0 | Already aquasecurity GitHub releases latest |
| Temurin JRE 21 | apt-latest | apt-latest | Adoptium apt repo always pulls latest |

## `.trivyignore` changes

- **Removed 1**: `CVE-2025-68121` — Go SDK bump fixes it. Replaced with a tombstone comment explaining the removal so a future audit can cross-reference.
- **Updated 3** (re-evaluate date 2026-11-06 → 2026-11-09; ORT 85.0.0 → 85.1.1 reach-surface header):
  - `CVE-2024-57699` — json-smart-2.5.0 in ORT (still bundled in 85.1.1)
  - `CVE-2026-5598` — bcprov-jdk18on inside jruby-stdlib
  - `CVE-2026-21452` — msgpack-core 0.9.10 in ORT
- **Added 0** — no new ignores. If CI image-scan after merge reveals new HIGH findings from the bumps (unexpected), follow-up commit.

## CI workflow change

`.github/workflows/ci.yml` `image-scan` job:

- **Before**: two sequential Trivy steps — `severity: CRITICAL` (hard-fail) + `severity: HIGH` (advisory, `continue-on-error: true`).
- **After**: single Trivy step — `severity: CRITICAL,HIGH` with `exit-code: "1"` (hard-fail).

The job-level comment block was also rewritten to reflect the current single-gate model rather than the two-step stop-gap from chore PR #25.

## Local verification

Attempted local docker build — colima daemon ran out of build cache disk inside the VM (~98% reclaimable images / 47GB stopped containers occupying the volume). Did NOT prune since the workspace contains in-use containers (Compose dev stack). Per the prompt's explicit fallback ("if docker build / trivy 환경이 로컬에 없으면, CI 에서 검증") the verification is delegated to the CI image-scan step on PR push.

If the CI image-scan fails on a HIGH finding I missed:
1. Read the Trivy table for the new HIGH CVE.
2. Decide upstream-fix-available (bump again) vs. runtime-unreached (`.trivyignore` with reach analysis).
3. Follow-up commit on the same branch.

## Files changed

- `apps/backend/Dockerfile.worker` — 5 ENV blocks bumped + license header line.
- `.trivyignore` — 1 entry removed (tombstone), 3 entries refreshed, ORT version line refreshed.
- `.github/workflows/ci.yml` — `image-scan` job comment rewrite + Trivy step merge.
- `docs/chore-backlog.md` — Chore P entry struck-through with completion summary.
- `docs/sessions/2026-05-09-chore-p-worker-image-refresh.md` — this file.

## Open follow-ups

- **CI image-scan first run** is the regression gate. If it surfaces a new HIGH the bump introduced, fix-forward.
- **ORT 86.x cut** (whenever upstream lands it): re-evaluate `.trivyignore` json-smart / msgpack-core entries. Likely all three ORT entries drop out together when 86.x bumps the underlying jars.
- **cdxgen-plugins-bin carve-out** (Phase 8 hardening backlog from earlier sessions): still open. The 12 plugins-bin entries in `.trivyignore` are the right surface to revisit when this lands.
- **Gradle 9.x** (deferred to future): if any downstream consumer needs a Gradle 9-only feature, re-evaluate the 8.x → 9.x trade-off.
